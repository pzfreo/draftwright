//! A publication-only permutation: placement, routes, paper and identities stay legacy.
use super::*;

const BASIS: &str = "; tags numbered spatially after layout (rows within 0.000001 model mm, then columns from the origin)";
const SAME_ROW: f64 = 1e-6;

fn family(tag: &str) -> &str {
    tag.find(|c: char| c.is_ascii_digit())
        .map_or("", |i| &tag[..i])
}

fn tag_box(b: &Balloon) -> Rect {
    let ink = crate::dims::label_ink(std::slice::from_ref(&b.tag), crate::dims::font());
    let w = (ink.max[0] - ink.min[0]).max(b.tag.len() as f64 * crate::dims::TEXT * 0.6);
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
                        p.cells_at[r][c] = (!text.trim().is_empty()).then(|| {
                            [
                                (p.columns[c] + p.columns[c + 1]) / 2.,
                                p.cells[3] - (r as f64 + 0.5) * ROW,
                            ]
                        });
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
        // Keep the compound tag's legacy member order, too.
        b.tag = b
            .tag
            .split('/')
            .map(|s| map.get(s).copied().unwrap_or(s))
            .collect::<Vec<_>>()
            .join("/");
        if b.label_box.is_some() {
            b.label_box = Some(tag_box(b));
        }
    }
}

/// Public recover(plan(...)) must feed the original presentation back into the solver.
pub(crate) fn restore(plan: &mut Plan) {
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
            if b.label_box.is_some() {
                b.label_box = Some(tag_box(b));
            }
        }
        for r in &mut t.rows {
            if let Some(id) = r.tag_id.take() {
                r.tag = id;
                r.cells[0] = r.tag.clone();
            }
        }
        if t.view.is_some() {
            t.rows.sort_by(|a, b| {
                a.cells[1]
                    .cmp(&b.cells[1])
                    .then_with(|| a.feature.cmp(&b.feature))
            });
        } else {
            t.rows.sort_by(|a, b| {
                a.tag
                    .len()
                    .cmp(&b.tag.len())
                    .then_with(|| a.tag.cmp(&b.tag))
            });
        }
        cells(t);
        t.basis = t.basis.replace(BASIS, "");
    }
    plan.problems
        .retain(|p| p.code != "hole_table_tag_order_retained");
}

fn permute(analysis: &Analysis, plan: &Plan, t: &mut HoleTable) -> bool {
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
        ordered.sort_by(|a, b| {
            a.2.total_cmp(&b.2)
                .then_with(|| a.1.total_cmp(&b.1))
                .then_with(|| t.rows[a.0].feature.cmp(&t.rows[b.0].feature))
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
                    .then_with(|| t.rows[a.0].feature.cmp(&t.rows[b.0].feature))
            });
            first = end;
        }
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
) -> Option<&'static str> {
    for b in after.balloons.iter().filter(|b| {
        belongs(b, t)
            && b.features
                .iter()
                .any(|f| t.rows.iter().any(|r| &r.feature == f))
    }) {
        let old = before.balloons.iter().find(|a| a.id == b.id)?;
        let (a, z) = (tag_box(old), tag_box(b));
        if z[2] - z[0] <= a[2] - a[0] + 1e-9 {
            continue;
        }
        let sheet = after.views.iter().find(|v| v.id == b.view)?.sheet;
        let page = std::iter::once(&after.sheet)
            .chain(&after.sheets)
            .find(|s| s.number == sheet)?
            .page;
        if z[0] < crate::layout::FRAME
            || z[1] < crate::layout::FRAME
            || z[2] > page[0] - crate::layout::FRAME
            || z[3] > page[1] - crate::layout::FRAME
        {
            return Some("a wider tag would cross the sheet frame");
        }
        if b.radius > 0.
            && ((z[2] - z[0]) / 2. >= b.radius - RING || (z[3] - z[1]) / 2. >= b.radius - RING)
        {
            return Some("a wider tag would cross its fixed ring");
        }
        if crate::dims::inspection::tag_blocker(after, sheet, &b.id, z).is_some()
            || b.leader.is_some_and(|l| crosses(l, z))
        {
            return Some("a wider tag would collide with fixed annotation ink or a leader");
        }
        if after.views.iter().any(|v| {
            v.id != b.view && v.sheet == sheet && overlap(z, Frame::new(analysis, v).outline)
        }) {
            return Some("a wider tag would collide with another view");
        }
        let view = after.views.iter().find(|v| v.id == b.view)?;
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
            return Some("a wider tag would collide with the projected part");
        }
    }
    for block in &t.blocks {
        if let Some(p) = &block.placed {
            for row in block.rows.iter().skip(if t.view.is_some() { 2 } else { 1 }) {
                for (c, text) in row.iter().enumerate() {
                    let ink = if t.view.is_some() && c == 1 {
                        size_ink(text, crate::dims::font())
                    } else {
                        tables::text_ink(text, crate::dims::font())
                    };
                    if ink.max[0] - ink.min[0]
                        > p.columns[c + 1] - p.columns[c] - 2. * tables::PAD + 1e-6
                    {
                        return Some("a reordered cell would collide with its fixed column rules");
                    }
                }
            }
        }
    }
    // A row-bound inspection leader is already routed. Moving its carrier is not relabelling.
    let old = before
        .hole_table
        .iter()
        .chain(&before.hole_tables)
        .find(|a| a.id == t.id)?;
    if t.rows.iter().zip(&old.rows).any(|(a, b)| {
        a.key() != b.key()
            && after
                .inspection_balloons
                .iter()
                .any(|ring| ring.item == format!("{}/{}", t.id, b.key()))
    }) {
        return Some("row reordering would detach a fixed inspection leader from its requirement");
    }
    None
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
        if let Some(why) = blocked(analysis, plan, &trial, &table) {
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
        restore(&mut p);
        assert_eq!(value(&p), value(original));
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
