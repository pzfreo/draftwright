//! An opt-in on-disk memo of finished plans and of the analyses they are made from, so that
//! processes planning the same part under the same Spec (CI's test shards and acceptance
//! measurements) analyse and plan it once and read it after.
//!
//! A plan is keyed by the engine's source fingerprint (`DRAFTWRIGHT_ENGINE_VERSION`: every
//! source file, asset, the manifest and the lockfile), the planning function, and the complete
//! Analysis and Spec as JSON; an analysis by the fingerprint, the file's name and its bytes. So
//! an entry is only ever read by the engine that made it, for exactly the inputs it was made
//! from. A miss computes as usual; refusals are not stored.
//!
//! A plan's entry holds its JSON and the planning state the JSON leaves out (table escalation,
//! callout geometry, axis overruns, each problem's fixes as held). Every entry is only written
//! when reading it back gives a value identical to the one made, field for field (`Debug`), so
//! a cached plan is the plan; a refused entry leaves a `.refused` note saying where it differs.
//!
//! Nothing here is used unless asked for: the functions take the directory, and the command
//! line reads `DRAFTWRIGHT_PLAN_CACHE` ([`dir`]).
use std::collections::BTreeMap;
use std::fmt::Debug;
use std::fs::{self, File};
use std::io::Write as _;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::haecceity::Part;
use crate::hole_table::TableOutcome;
use crate::model::{self, Analysis, Invalid, Plan, Spec, Unanalysed};

/// The variable naming the command line's plan cache directory.
pub const VARIABLE: &str = "DRAFTWRIGHT_PLAN_CACHE";
const FORMAT: &str = "draftwright.plan-cache/2";

/// The command line's plan cache directory: `DRAFTWRIGHT_PLAN_CACHE`, when set and not empty.
pub fn dir() -> Option<PathBuf> {
    std::env::var_os(VARIABLE)
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
}

/// [`model::plan_recovered`], read from or written to the cache in *dir* when given.
pub fn plan_recovered_in(
    dir: Option<&Path>,
    analysis: &Analysis,
    spec: &Spec,
) -> Result<Plan, Invalid> {
    planned(dir, "recovered", analysis, spec, model::plan_recovered)
}

/// [`model::plan`], read from or written to the cache in *dir* when given.
pub fn plan_in(dir: Option<&Path>, analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    planned(dir, "plan", analysis, spec, model::plan)
}

/// [`model::analyse_native`], its Analysis read from or written to the cache in *dir* when
/// given. On a hit the part is read from the file again, which is a small share of analysing
/// it: the same part, its lazily computed surface grids not yet computed.
pub fn analyse_native_in(dir: Option<&Path>, path: &Path) -> Result<(Part, Analysis), Unanalysed> {
    analysed_in(dir, path, &[b"analyse_native"], || {
        model::analyse_native(path)
    })
}

/// *compute*'s part and Analysis of the STEP file at *path* with no part of an assembly chosen
/// (the whole file, or the part `binding.part_index` names when its bodies coincide), the
/// Analysis read from or written to the cache in *dir* when given, keyed by the file's name and
/// bytes and *recipe*: which reading *compute* makes, with every other input it reads
/// (recognition JSON, for one). On a hit the part is read from the file again.
pub fn analysed_in<E>(
    dir: Option<&Path>,
    path: &Path,
    recipe: &[&[u8]],
    compute: impl FnOnce() -> Result<(Part, Analysis), E>,
) -> Result<(Part, Analysis), E> {
    let Some(dir) = dir else {
        return compute();
    };
    let Ok(bytes) = fs::read(path) else {
        return compute();
    };
    let name = path.file_name().unwrap_or_default().to_string_lossy();
    let mut inputs: Vec<&[u8]> = vec![b"analysis", name.as_bytes(), &bytes];
    inputs.extend(recipe);
    let mut computed = None;
    let mut compute = Some(compute);
    let analysis = memo(
        dir,
        &key(&inputs),
        "analysis",
        |a: &Analysis| serde_json::to_vec(a).ok(),
        |text| serde_json::from_slice(text).ok(),
        || {
            let (p, a) = (compute.take().unwrap())()?;
            computed = Some(p);
            Ok(a)
        },
    )?;
    match computed {
        Some(part) => Ok((part, analysis)),
        // The part the Analysis was read from: one part alone when every body coincided with
        // it (#212), set square to the file's axes when it was (#216).
        None => {
            match model::step_bytes(&bytes)
                .ok()
                .and_then(|b| match analysis.binding.part_index {
                    None => crate::haecceity::read_step(&b).ok(),
                    Some(k) => crate::haecceity::step::StepFile::read(&b)
                        .and_then(|file| file.part(k))
                        .ok(),
                })
                .and_then(|part| model::as_analysed(part, &analysis).ok())
            {
                Some(part) => Ok((part, analysis)),
                None => (compute.take().unwrap())(),
            }
        }
    }
}

fn planned(
    dir: Option<&Path>,
    kind: &str,
    analysis: &Analysis,
    spec: &Spec,
    compute: fn(&Analysis, &Spec) -> Result<Plan, Invalid>,
) -> Result<Plan, Invalid> {
    // Pin replay can depend on a reader's verified B-rep, which is not an input
    // to this disk cache. Re-evaluate it instead of persisting a missing proof.
    if spec.engine_pins.is_some() {
        return compute(analysis, spec);
    }
    let Some(dir) = dir else {
        return compute(analysis, spec);
    };
    memo(
        dir,
        &plan_key(kind, analysis, spec),
        "plan",
        |plan: &Plan| serde_json::to_vec(&Entry::of(plan)).ok(),
        |text| {
            serde_json::from_slice::<Entry>(text)
                .ok()
                .map(Entry::into_plan)
        },
        || compute(analysis, spec),
    )
}

fn plan_key(kind: &str, analysis: &Analysis, spec: &Spec) -> String {
    key(&[
        kind.as_bytes(),
        &serde_json::to_vec(analysis).expect("an Analysis serialises"),
        &serde_json::to_vec(spec).expect("a Spec serialises"),
    ])
}

/// The entry's file name: the digest of the engine and of everything the value is a function of.
fn key(inputs: &[&[u8]]) -> String {
    let mut hash = Sha256::new();
    let engine: [&[u8]; 5] = [
        FORMAT.as_bytes(),
        env!("DRAFTWRIGHT_ENGINE_VERSION").as_bytes(),
        std::env::consts::ARCH.as_bytes(),
        std::env::consts::OS.as_bytes(),
        if cfg!(debug_assertions) {
            b"debug"
        } else {
            b"release"
        },
    ];
    for part in engine.iter().chain(inputs) {
        hash.update((part.len() as u64).to_le_bytes());
        hash.update(part);
    }
    hash.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// The value stored under *key* in *dir*, or *compute*'s, stored when it reads back identically.
fn memo<T: Debug, E>(
    dir: &Path,
    key: &str,
    kind: &str,
    encode: impl Fn(&T) -> Option<Vec<u8>>,
    decode: impl Fn(&[u8]) -> Option<T>,
    compute: impl FnOnce() -> Result<T, E>,
) -> Result<T, E> {
    let path = dir.join(format!("{kind}-{key}.json"));
    let read = || fs::read(&path).ok().and_then(|text| decode(&text));
    if let Some(value) = read() {
        return Ok(value);
    }
    // One process computes a key while the others wanting it wait, then read its entry. The
    // lock is advisory and only orders work: without it (or on any I/O error) each computes.
    let lock = fs::create_dir_all(dir)
        .and_then(|()| File::create(dir.join(format!("{kind}-{key}.lock"))))
        .ok()
        .filter(|f| f.lock().is_ok());
    if lock.is_some()
        && let Some(value) = read()
    {
        return Ok(value);
    }
    let value = compute()?;
    if let Some(text) = encode(&value) {
        write(
            dir,
            &path,
            &text,
            decode(&text).map(|v| format!("{v:?}")),
            &value,
        );
    }
    drop(lock);
    Ok(value)
}

/// Stores *text* when what it decodes to (*read*) is identical to *made*; otherwise notes where
/// they differ beside the entry, and stores nothing.
fn write<T: Debug>(dir: &Path, path: &Path, text: &[u8], read: Option<String>, made: &T) {
    let made = format!("{made:?}");
    let read = read.unwrap_or_default();
    if read != made {
        let at = read
            .bytes()
            .zip(made.bytes())
            .take_while(|(r, m)| r == m)
            .count();
        let context = |s: &str| {
            s.get(at.saturating_sub(200)..(at + 200).min(s.len()))
                .unwrap_or("")
                .to_string()
        };
        let _ = fs::write(
            path.with_extension("refused"),
            format!("read: {}\nmade: {}\n", context(&read), context(&made)),
        );
        return;
    }
    let staged = dir.join(format!(
        ".{}.{}.tmp",
        path.file_name().unwrap().to_string_lossy(),
        std::process::id()
    ));
    let done = File::create(&staged)
        .and_then(|mut f| f.write_all(text))
        .and_then(|()| fs::rename(&staged, path));
    if done.is_err() {
        let _ = fs::remove_file(&staged);
    }
}

/// A plan and the planning state its JSON leaves out.
#[derive(Serialize, Deserialize)]
struct Entry {
    plan: Plan,
    hidden: Hidden,
}

#[derive(Serialize, Deserialize)]
struct Hidden {
    view_poses: Vec<Option<model::LinePose>>,
    gdt_poses: Vec<Option<crate::dims::gdt::PlacementPose>>,
    table_outcome: u8,
    table_restore: Option<Box<Entry>>,
    callout_widths: BTreeMap<String, f64>,
    callout_tips: BTreeMap<String, (String, [f64; 2])>,
    dimension_labels: BTreeMap<String, (String, [f64; 2])>,
    /// Each view's gutter translation, which a pin replay of this plan spends.
    gutter_deltas: BTreeMap<String, [f64; 2]>,
    /// Each view's line work's overruns, in order.
    overruns: Vec<Vec<f64>>,
    /// Each problem's fixes as held: a problem's JSON states its offered fixes instead.
    problems: Vec<RawProblem>,
}

#[derive(Serialize, Deserialize)]
struct RawProblem {
    fix: Vec<crate::items::Op>,
    fixes: Vec<RawFix>,
}

#[derive(Serialize, Deserialize)]
struct RawFix {
    description: String,
    ops: Vec<crate::items::Op>,
    minimum_scope: Option<crate::remedy_scope::Scope>,
}

impl Entry {
    fn of(plan: &Plan) -> Self {
        Entry {
            plan: plan.clone(),
            hidden: Hidden {
                view_poses: plan.views.iter().map(|v| v.line_pose.clone()).collect(),
                gdt_poses: plan
                    .gdt
                    .iter()
                    .map(|g| g.placed.as_ref().and_then(|p| p.pose.clone()))
                    .collect(),
                table_outcome: match plan.table_outcome {
                    TableOutcome::Kept => 0,
                    TableOutcome::NoPlace => 1,
                    TableOutcome::Balloons => 2,
                    TableOutcome::None => 3,
                },
                table_restore: plan
                    .table_restore
                    .as_deref()
                    .map(|p| Box::new(Entry::of(p))),
                callout_widths: plan.callout_widths.clone(),
                callout_tips: plan.callout_tips.clone(),
                dimension_labels: plan.dimension_labels.clone(),
                gutter_deltas: plan.gutter_deltas.clone(),
                overruns: plan
                    .views
                    .iter()
                    .map(|v| v.line_work.iter().map(|w| w.overrun).collect())
                    .collect(),
                problems: plan
                    .problems
                    .iter()
                    .map(|p| RawProblem {
                        fix: p.fix.clone(),
                        fixes: p
                            .fixes
                            .iter()
                            .map(|f| RawFix {
                                description: f.description.clone(),
                                ops: f.ops.clone(),
                                minimum_scope: f.minimum_scope,
                            })
                            .collect(),
                    })
                    .collect(),
            },
        }
    }

    fn into_plan(self) -> Plan {
        let Entry { mut plan, hidden } = self;
        plan.table_outcome = match hidden.table_outcome {
            0 => TableOutcome::Kept,
            1 => TableOutcome::NoPlace,
            2 => TableOutcome::Balloons,
            _ => TableOutcome::None,
        };
        plan.table_restore = hidden.table_restore.map(|e| Box::new(e.into_plan()));
        for (view, pose) in plan.views.iter_mut().zip(hidden.view_poses) {
            view.line_pose = pose;
        }
        for (gdt, pose) in plan.gdt.iter_mut().zip(hidden.gdt_poses) {
            if let Some(placed) = &mut gdt.placed {
                placed.pose = pose;
            }
        }
        plan.callout_widths = hidden.callout_widths;
        plan.callout_tips = hidden.callout_tips;
        plan.dimension_labels = hidden.dimension_labels;
        plan.gutter_deltas = hidden.gutter_deltas;
        for (view, overruns) in plan.views.iter_mut().zip(hidden.overruns) {
            for (work, overrun) in view.line_work.iter_mut().zip(overruns) {
                work.overrun = overrun;
            }
        }
        for (problem, raw) in plan.problems.iter_mut().zip(hidden.problems) {
            problem.fix = raw.fix;
            problem.fixes = raw
                .fixes
                .into_iter()
                .map(|f| crate::fixes::Fix {
                    description: f.description,
                    ops: f.ops,
                    minimum_scope: f.minimum_scope,
                })
                .collect();
        }
        plan
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A cached plan is the plan: written on the first call, read on the second, identical in
    /// every field to planning afresh (including what the plan's JSON leaves out); another Spec
    /// or planning function is another entry.
    #[test]
    fn a_cached_plan_is_the_plan_and_other_inputs_miss() {
        let dir =
            std::env::temp_dir().join(format!("draftwright-plan-cache-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts");
        for file in ["mounting_plate.step", "turned.step", "scattered_holes.step"] {
            let (_, analysis) = model::analyse_native(&root.join(file)).unwrap();
            let spec = Spec::default();
            let fresh = model::plan_recovered(&analysis, &spec).unwrap();
            let first = plan_recovered_in(Some(&dir), &analysis, &spec).unwrap();
            let entry = dir.join(format!(
                "plan-{}.json",
                plan_key("recovered", &analysis, &spec)
            ));
            assert!(entry.is_file(), "{file}: the plan is stored");
            let second = plan_recovered_in(Some(&dir), &analysis, &spec).unwrap();
            assert_eq!(format!("{first:?}"), format!("{fresh:?}"), "{file}");
            assert_eq!(
                format!("{second:?}"),
                format!("{fresh:?}"),
                "{file}: read back"
            );
            assert!(
                fresh.views.iter().any(|v| !v.line_work.is_empty())
                    || !fresh.callout_tips.is_empty(),
                "{file}: the part exercises planning state the plan's JSON leaves out"
            );
            assert_ne!(
                plan_key("plan", &analysis, &spec),
                plan_key("recovered", &analysis, &spec)
            );
            let mut other = spec.clone();
            other.sheet.view_gutter = Some(3.0);
            assert_ne!(
                plan_key("recovered", &analysis, &other),
                plan_key("recovered", &analysis, &spec)
            );
            let direct = plan_in(Some(&dir), &analysis, &spec).unwrap();
            let direct_fresh = model::plan(&analysis, &spec).unwrap();
            assert_eq!(
                format!("{direct:?}"),
                format!("{direct_fresh:?}"),
                "{file}: plan"
            );
        }
        let _ = fs::remove_dir_all(&dir);
    }

    /// A cached analysis is the analysis, and the part read again beside it draws the same
    /// drawing as the part analysed afresh.
    #[test]
    fn a_cached_analysis_is_the_analysis_and_its_part_draws_the_same() {
        let dir =
            std::env::temp_dir().join(format!("draftwright-analysis-cache-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let font = crate::text::Font::new(crate::api::MONO.to_vec()).unwrap();
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts");
        for file in [
            "mounting_plate.step",
            "turned.step",
            "manufacturing_pmi.step.gz",
        ] {
            let path = root.join(file);
            let (part, fresh) = model::analyse_native(&path).unwrap();
            let (_, first) = analyse_native_in(Some(&dir), &path).unwrap();
            let (read, second) = analyse_native_in(Some(&dir), &path).unwrap();
            assert_eq!(
                dir.read_dir()
                    .unwrap()
                    .filter(|e| e
                        .as_ref()
                        .unwrap()
                        .path()
                        .extension()
                        .is_some_and(|x| x == "refused"))
                    .count(),
                0
            );
            assert_eq!(format!("{first:?}"), format!("{fresh:?}"), "{file}");
            assert_eq!(
                format!("{second:?}"),
                format!("{fresh:?}"),
                "{file}: read back"
            );
            let plan = model::plan_recovered(&fresh, &Spec::default()).unwrap();
            let drawn = |p: &Part| {
                format!(
                    "{:?}",
                    model::render_sheets(p, &fresh, &plan, &font).unwrap()
                )
            };
            assert_eq!(drawn(&read), drawn(&part), "{file}: the part read again");
        }
        let _ = fs::remove_dir_all(&dir);
    }
}
