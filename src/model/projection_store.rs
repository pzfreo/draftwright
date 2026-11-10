//! Stable disk representation of kernel projections; no changes to the kernel's API.
use super::*;
use haecceity::hlr::{Class, Exact};

#[derive(Serialize, Deserialize)]
#[serde(remote = "Exact")]
enum ExactDef {
    Line([f64; 2], [f64; 2]),
    Conic {
        centre: [f64; 2],
        radii: [f64; 2],
        x_axis: [f64; 2],
        y_axis: [f64; 2],
        t0: f64,
        t1: f64,
    },
    Nurbs {
        degree: usize,
        poles: Vec<[f64; 2]>,
        weights: Vec<f64>,
        knots: Vec<f64>,
        t0: f64,
        t1: f64,
    },
}
#[derive(Serialize, Deserialize)]
struct ExactValue(#[serde(with = "ExactDef")] Exact);
#[derive(Serialize, Deserialize)]
#[serde(remote = "Class")]
enum ClassDef {
    Sharp,
    Smooth,
    Outline,
}
#[derive(Serialize, Deserialize)]
struct Piece {
    #[serde(with = "ClassDef")]
    class: Class,
    visible: bool,
    points: Vec<[f64; 2]>,
    exact: Option<ExactValue>,
}
#[derive(Serialize, Deserialize)]
struct Stored {
    binding: Option<String>,
    views: BTreeMap<Vec<u64>, Vec<Piece>>,
    cuts: BTreeMap<Vec<u64>, Vec<Vec<[f64; 3]>>>,
    geometry: BTreeMap<Vec<u64>, (Vec<u64>, ViewGeometry)>,
    cut_material: BTreeMap<Vec<u64>, bool>,
    hatches: BTreeMap<Vec<u64>, Vec<Item>>,
}
// JSON map keys are strings; sorted pairs retain numeric cache keys without hash iteration order.
type CutPairs = Vec<(Vec<u64>, Vec<Vec<[f64; 3]>>)>;
type GeometryPairs = Vec<(Vec<u64>, (Vec<u64>, ViewGeometry))>;
#[derive(Serialize, Deserialize)]
struct Pairs {
    binding: Option<String>,
    views: Vec<(Vec<u64>, Vec<Piece>)>,
    cuts: CutPairs,
    geometry: GeometryPairs,
    #[serde(default)]
    cut_material: Vec<(Vec<u64>, bool)>,
    #[serde(default)]
    hatches: Vec<(Vec<u64>, Vec<Item>)>,
}
impl Projections {
    pub fn disk_bytes(&self) -> Result<Vec<u8>, serde_json::Error> {
        let stored = Stored {
            binding: self.binding.clone(),
            views: self
                .views
                .iter()
                .map(|(k, v)| {
                    (
                        k.clone(),
                        v.iter()
                            .map(|p| Piece {
                                class: p.class,
                                visible: p.visible,
                                points: p.points.clone(),
                                exact: p.exact.clone().map(ExactValue),
                            })
                            .collect(),
                    )
                })
                .collect(),
            cuts: self.cuts.clone().into_iter().collect(),
            geometry: self.geometry.clone().into_iter().collect(),
            cut_material: self.cut_material.clone().into_iter().collect(),
            hatches: self.hatches.clone().into_iter().collect(),
        };
        serde_json::to_vec(&Pairs {
            binding: stored.binding,
            views: stored.views.into_iter().collect(),
            cuts: stored.cuts.into_iter().collect(),
            geometry: stored.geometry.into_iter().collect(),
            cut_material: stored.cut_material.into_iter().collect(),
            hatches: stored.hatches.into_iter().collect(),
        })
    }
    pub fn from_disk(bytes: &[u8]) -> Result<Self, serde_json::Error> {
        // Warm stateless requests read the same immutable file. Key successful parses
        // by every byte; changed or damaged files must still be parsed and checked.
        // Limit both entry count and input size rather than retaining large assemblies.
        static MEMO: crate::planning::BoundedMemo<Projections> =
            crate::planning::BoundedMemo::new(4);
        let key: Option<[u8; 32]> =
            (bytes.len() <= 8 * 1024 * 1024).then(|| Sha256::digest(bytes).into());
        if let Some(projections) = key.and_then(|key| MEMO.get(&key)) {
            return Ok(projections);
        }
        let p: Pairs = serde_json::from_slice(bytes)?;
        let projections = Self {
            binding: p.binding,
            views: p
                .views
                .into_iter()
                .map(|(k, v)| {
                    (
                        k,
                        v.into_iter()
                            .map(|p| Projected {
                                class: p.class,
                                visible: p.visible,
                                points: p.points,
                                exact: p.exact.map(|e| e.0),
                            })
                            .collect(),
                    )
                })
                .collect(),
            cuts: p.cuts.into_iter().collect(),
            geometry: p.geometry.into_iter().collect(),
            cut_material: p.cut_material.into_iter().collect(),
            hatches: p.hatches.into_iter().collect(),
            shaded_mesh: None,
            #[cfg(test)]
            shaded_fragment_limit: None,
            stats: ProjectionStats::default(),
            dirty: false,
        };
        if let Some(key) = key {
            MEMO.insert(key, projections.clone());
        }
        Ok(projections)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::Features;
    use crate::items::Op;
    use crate::session::Session;

    #[test]
    fn a_loaded_cache_stays_clean_for_title_edits_and_tracks_changed_geometry() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/turned.step");
        let initial = Session::open(&path, Features::Native, Spec::default()).unwrap();
        assert!(initial.projections().dirty);
        let bytes = initial.projections().disk_bytes().unwrap();
        let loaded = Projections::from_disk(&bytes).unwrap();
        assert!(!loaded.dirty);
        let mut session = Session::with_projections(
            haecceity::read_step(&std::fs::read(&path).unwrap()).unwrap(),
            initial.analysis().clone(),
            initial.spec().clone(),
            loaded,
        )
        .unwrap();
        assert!(!session.projections().dirty);
        session
            .apply(
                &[Op::set("sheet/title/material", serde_json::json!("Steel"))],
                true,
            )
            .unwrap();
        assert!(!session.projections().dirty);
        assert_eq!(session.projections().disk_bytes().unwrap(), bytes);
        // A4-first can already select 1:1; request a different scale to exercise a miss.
        let scale = if session.plan().sheet.scale == "1:1" {
            "2:1"
        } else {
            "1:1"
        };
        session
            .apply(&[Op::set("sheet/scale", serde_json::json!(scale))], true)
            .unwrap();
        assert!(session.projections().dirty);
        assert_ne!(session.projections().disk_bytes().unwrap(), bytes);
        let fresh = Session::open(&path, Features::Native, session.spec().clone()).unwrap();
        assert_eq!(session.drawing().svg(), fresh.drawing().svg());
        let again = Projections::from_disk(&bytes).unwrap();
        assert!(!again.dirty);
        assert_eq!(again.stats(), ProjectionStats::default());
        assert_eq!(again.disk_bytes().unwrap(), bytes);
    }
}
