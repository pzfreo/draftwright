//! Furniture pins contain coordinates only; text and claims come from the current plan.
use super::{Annotation, EnginePins, Route};
use crate::{model::Plan, placement_intent::Place};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Grid {
    pub x: Vec<f64>,
    pub y: Vec<Option<f64>>,
    pub bounds: [f64; 4],
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub children: Vec<Grid>,
}
impl Grid {
    fn of(p: &crate::tables::PlacedTable) -> Self {
        Self {
            x: p.columns.clone(),
            y: p.cells_at
                .iter()
                .map(|r| r.iter().flatten().next().map(|p| p[1]))
                .collect(),
            bounds: p.cells,
            children: p.blocks.iter().map(Self::of).collect(),
        }
    }
    fn fits(&self, p: &crate::tables::PlacedTable) -> bool {
        self.x.len() == p.columns.len()
            && self.y.len() == p.cells_at.len()
            && self.children.len() == p.blocks.len()
            && self
                .x
                .windows(2)
                .zip(p.columns.windows(2))
                .all(|(old, new)| new[1] - new[0] <= old[1] - old[0] + 1e-8)
            && self
                .children
                .iter()
                .zip(&p.blocks)
                .all(|(old, new)| old.fits(new))
    }
    fn apply(&self, p: &mut crate::tables::PlacedTable) {
        for (r, row) in p.cells_at.iter_mut().enumerate() {
            for (c, at) in row.iter_mut().enumerate() {
                if let Some(at) = at {
                    at[0] = 0.5 * (self.x[c] + self.x[c + 1]);
                    if let Some(y) = self.y[r] {
                        at[1] = y;
                    }
                }
            }
        }
        p.columns.clone_from(&self.x);
        p.cells = self.bounds;
        for (pin, block) in self.children.iter().zip(&mut p.blocks) {
            pin.apply(block);
        }
    }
}
fn pin(route: Route, view: Option<String>) -> Annotation {
    Annotation {
        place: Place {
            view,
            pinned: true,
            ..Default::default()
        },
        route: Some(route),
        basis: None,
        space: None,
        omit: false,
        region: None,
        absolute: true,
        introduced: false,
    }
}
pub(super) fn persist(plan: &Plan, pins: &mut BTreeMap<String, Annotation>) {
    for note in &plan.notes {
        if let Some(p) = &note.placed {
            pins.insert(
                note.id.clone(),
                pin(
                    Route::Note {
                        at: p.rows_at.clone(),
                        bounds: p.cells,
                    },
                    None,
                ),
            );
        }
    }
    for (id, block) in [
        ("notes", &plan.notes_block),
        ("radius_notes", &plan.radius_notes_block),
    ] {
        if let Some(block) = block {
            pins.insert(
                id.into(),
                pin(
                    Route::Heading {
                        at: block.title_at,
                        bounds: block.cells,
                    },
                    None,
                ),
            );
        }
    }
    for table in plan
        .tables
        .iter()
        .chain(plan.radius_table.iter().map(|t| &t.table))
    {
        if let Some(p) = &table.placed {
            pins.insert(
                table.id.clone(),
                pin(Route::Grid { grid: Grid::of(p) }, None),
            );
        }
    }
    for caption in &plan.captions {
        let mut p = pin(
            Route::Marker {
                leader: None,
                bounds: Some(caption.ink),
            },
            Some(caption.view.clone()),
        );
        p.place.offset = Some(caption.at);
        pins.insert(caption.id.clone(), p);
    }
}
pub(super) fn apply(plan: &mut Plan, pins: &EnginePins, remaining: &mut BTreeSet<String>) {
    for note in &mut plan.notes {
        if let Some(Route::Note { at, bounds }) = pins
            .annotations
            .get(&note.id)
            .and_then(|p| p.route.as_ref())
            && note.rows.len() == at.len()
        {
            note.placed = Some(crate::notes::PlacedNote {
                rows_at: at.clone(),
                cells: *bounds,
            });
            note.state = crate::items::State::Shown;
            remaining.remove(&note.id);
        }
    }
    for (id, block) in [
        ("notes", &mut plan.notes_block),
        ("radius_notes", &mut plan.radius_notes_block),
    ] {
        if let (Some(Route::Heading { at, bounds }), Some(block)) = (
            pins.annotations.get(id).and_then(|p| p.route.as_ref()),
            block,
        ) {
            block.title_at = *at;
            block.cells = *bounds;
            remaining.remove(id);
        }
    }
    for table in plan
        .tables
        .iter_mut()
        .chain(plan.radius_table.iter_mut().map(|t| &mut t.table))
    {
        if let (Some(Route::Grid { grid }), Some(p)) = (
            pins.annotations
                .get(&table.id)
                .and_then(|p| p.route.as_ref()),
            &mut table.placed,
        ) && grid.fits(p)
        {
            grid.apply(p);
            remaining.remove(&table.id);
        }
    }
    for caption in &mut plan.captions {
        if let Some(pin) = pins.annotations.get(&caption.id)
            && let (
                Some(at),
                Some(Route::Marker {
                    bounds: Some(bounds),
                    ..
                }),
            ) = (pin.place.offset, &pin.route)
        {
            caption.at = at;
            caption.ink = *bounds;
            remaining.remove(&caption.id);
        }
    }
}
