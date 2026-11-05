//! Stateless operations over Session, with immutable content and a reusable disk cache.
use std::fs::{self, File, OpenOptions};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::api::{self, Error};
use crate::items::Op;
use crate::model::{Analysis, Projections, Spec};
use crate::session::{Delta, ItemAnswer, ItemFilter, Options, ProblemFilter, Session};

/// Content fingerprint of the compiled engine source, lockfile and embedded assets.
pub const ENGINE_VERSION: &str = env!("DRAFTWRIGHT_ENGINE_VERSION");
pub const PAGE_BYTES: usize = 4096;
pub const MAX_OPS: usize = 32;
static STAGED: AtomicU64 = AtomicU64::new(0);

#[cfg(test)]
pub(crate) mod work {
    use std::cell::Cell;
    thread_local! {
        static PLANS: Cell<usize> = const { Cell::new(0) };
    }
    pub fn plan() {
        PLANS.with(|n| n.set(n.get() + 1));
    }
    pub fn take() -> usize {
        PLANS.with(|n| n.replace(0))
    }
}

pub(crate) fn session_apply(
    session: &mut Session,
    ops: &[Op],
    commit: bool,
) -> Result<Delta, Error> {
    // Session evaluates exactly once after whole-Spec edit validation succeeds.
    #[cfg(test)]
    if crate::model::edit(session.spec(), &serde_json::to_value(ops).unwrap()).is_ok() {
        work::plan();
    }
    session.apply(ops, commit)
}

pub(crate) fn check_operation_count(count: usize) -> Result<(), Error> {
    if count > MAX_OPS {
        return Err(Error::invalid(format!(
            "ops accepts at most {MAX_OPS} submitted operations; split the request into smaller batches"
        )));
    }
    Ok(())
}

pub fn hash(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}
pub fn spec_hash(spec: &Spec) -> String {
    hash(&serde_json::to_vec(spec).expect("Spec serialises"))
}
fn io(e: std::io::Error) -> Error {
    Error::failed(format!(
        "cache I/O: {e}; retry with a writable cache directory"
    ))
}
fn encode<T: Serialize>(v: &T) -> Vec<u8> {
    serde_json::to_vec(v).expect("document serialises")
}
fn read<T: for<'a> Deserialize<'a>>(path: &Path) -> Result<T, Error> {
    serde_json::from_slice(&fs::read(path).map_err(io)?).map_err(|e| {
        Error::failed(format!(
            "invalid cache {}: {e}; remove the damaged entry and register the part again",
            path.display()
        ))
    })
}
fn atomic(path: &Path, bytes: &[u8]) -> Result<(), Error> {
    let temp = path.with_extension(format!(
        "{}.{}.tmp",
        std::process::id(),
        STAGED.fetch_add(1, Ordering::Relaxed)
    ));
    use std::io::Write;
    let result = (|| {
        let mut f = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)
            .map_err(io)?;
        f.write_all(bytes).map_err(io)?;
        f.sync_all().map_err(io)?;
        fs::rename(&temp, path).map_err(io)
    })();
    let _ = fs::remove_file(temp);
    result
}
fn safe(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 160
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

#[derive(Clone, Debug)]
pub struct Cache {
    pub root: PathBuf,
}
#[derive(Serialize, Deserialize)]
struct Registration {
    name: String,
    features: Option<String>,
    /// The part of an assembly registered alone (its index); absent for the whole file.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    part: Option<usize>,
}

/// The STEP SHA-256 a part_ref's first half carries: `<sha>`, or `<sha>_p<k>` for part *k* of
/// an assembly registered alone.
fn source_of(key: &str) -> Option<&str> {
    let sha = match key.split_once("_p") {
        Some((sha, k)) if !k.is_empty() && k.bytes().all(|b| b.is_ascii_digit()) => sha,
        Some(_) => return None,
        None => key,
    };
    (sha.len() == 64
        && sha
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()))
    .then_some(sha)
}
#[derive(Serialize)]
pub struct Applied {
    pub spec: Spec,
    pub delta: Delta,
}
impl Cache {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }
    pub fn environment() -> Self {
        Self::new(
            std::env::var_os("DRAFTWRIGHT_CACHE")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from(".cache/draftwright")),
        )
    }
    fn dir(&self, part_ref: &str) -> Result<PathBuf, Error> {
        let version = ENGINE_VERSION;
        let valid = part_ref
            .split_once('-')
            .is_some_and(|(sha, engine)| source_of(sha).is_some() && engine == version);
        if !valid {
            return Err(Error::invalid(
                "unknown part_ref or engine version; register the STEP with inspect_part {step: <path>} to obtain this engine's content key",
            ));
        }
        let dir = self.root.join("parts").join(part_ref);
        if !dir.join("registration.json").is_file() {
            return Err(Error::invalid(
                "unknown part_ref; register the STEP with inspect_part {step: <path>} using this cache",
            ));
        }
        Ok(dir)
    }
    fn lock(dir: &Path) -> Result<File, Error> {
        let file = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(dir.join("lock"))
            .map_err(io)?;
        file.lock().map_err(io)?;
        Ok(file)
    }
    pub fn register(&self, step: &Path, features: Option<&Path>) -> Result<String, Error> {
        self.register_part(step, features, None)
    }
    /// [`Cache::register`] of part *part* of an assembly alone, when given: its own key,
    /// `<sha>_p<part>-<engine>`. Refused, naming the parts, when the file has no such part.
    pub fn register_part(
        &self,
        step: &Path,
        features: Option<&Path>,
        part: Option<usize>,
    ) -> Result<String, Error> {
        // Gzip is decompressed: the key and the cached source.step are the STEP text's, so a
        // `.step` and its `.step.gz` are one registration.
        let bytes = crate::model::read_step_file(step)
            .map_err(|e| Error::invalid(format!("{e}; provide an existing STEP path")))?;
        if let Some(k) = part {
            let parts = crate::model::parts(step, &bytes)?;
            if k >= parts.len() {
                return Err(Error {
                    choices: (0..parts.len()).map(|p| p.to_string()).collect(),
                    ..Error::invalid(format!(
                        "{} has {} part(s) ({}); there is no part {k}",
                        step.display(),
                        parts.len(),
                        crate::model::part_list(&parts)
                    ))
                });
            }
        }
        let version = ENGINE_VERSION;
        let suffix = part.map_or(String::new(), |k| format!("_p{k}"));
        let key = format!("{}{suffix}-{version}", hash(&bytes));
        let dir = self.root.join("parts").join(&key);
        fs::create_dir_all(&dir).map_err(io)?;
        let _lock = Self::lock(&dir)?;
        let feature_text = features.map(|path|fs::read_to_string(path).map_err(|e|Error::invalid(format!("cannot read {}: {e}; provide recognition JSON for this STEP or omit features",path.display())))).transpose()?;
        if dir.join("registration.json").is_file() {
            let prior: Registration = read(&dir.join("registration.json"))?;
            if prior.features != feature_text {
                return Err(Error::invalid(
                    "this STEP key is already registered with different recognition; use the registered recognition, or a separate cache root for evaluation features",
                ));
            }
        } else {
            atomic(&dir.join("source.step"), &bytes)?;
            atomic(
                &dir.join("registration.json"),
                &encode(&Registration {
                    name: step
                        .file_name()
                        .unwrap_or_default()
                        .to_string_lossy()
                        .into_owned(),
                    features: feature_text,
                    part,
                }),
            )?;
        }
        Ok(key)
    }
    /// Original registered STEP bytes and filename, for whole-assembly consumers.
    /// A selected-part registration still retains the complete source assembly.
    pub(crate) fn source(&self, part_ref: &str) -> Result<(String, Vec<u8>), Error> {
        let dir = self.dir(part_ref)?;
        let _lock = Self::lock(&dir)?;
        let bytes = fs::read(dir.join("source.step")).map_err(io)?;
        if part_ref.split_once('-').and_then(|(key, _)| source_of(key))
            != Some(hash(&bytes).as_str())
        {
            return Err(Error::invalid(
                "cached STEP differs from part_ref; remove the damaged entry and register the original STEP again",
            ));
        }
        let registration: Registration = read(&dir.join("registration.json"))?;
        Ok((registration.name, bytes))
    }

    fn session_unlocked(&self, dir: &Path, spec: &Spec) -> Result<Session, Error> {
        let source = fs::read(dir.join("source.step")).map_err(io)?;
        let source_hash = hash(&source);
        if dir
            .file_name()
            .and_then(|n| n.to_str())
            .and_then(|n| n.split_once('-'))
            .is_none_or(|(key, _)| source_of(key) != Some(source_hash.as_str()))
        {
            return Err(Error::invalid(
                "cached STEP differs from part_ref; remove the damaged entry and register the original STEP again",
            ));
        }
        let registration: Registration = read(&dir.join("registration.json"))?;
        let (part, analysis) = if dir.join("analysis.json").is_file() {
            let analysis: Analysis = read(&dir.join("analysis.json"))?;
            if analysis.binding.source_sha256 != source_hash
                || analysis.schema != crate::model::ANALYSIS_SCHEMA
            {
                return Err(Error::invalid(
                    "cached Analysis differs from the registered STEP or engine; remove analysis.json and register again",
                ));
            }
            // A part chosen, or the one part every body coincided with (#212).
            let part = match registration.part.or(analysis.binding.part_index) {
                None => crate::haecceity::read_step(&source),
                Some(k) => crate::haecceity::step::StepFile::read(&source).and_then(|file| {
                    if k < file.parts.len() {
                        file.part(k)
                    } else {
                        Err(crate::haecceity::step::StepError::Unsupported(format!(
                            "there is no part {k}"
                        )))
                    }
                }),
            }
            // As the Analysis drew it: set square to the file's axes when it was (#216).
            .and_then(|part| crate::model::as_analysed(part, &analysis))
            .map_err(|e| {
                Error::failed(format!(
                    "cannot load cached STEP: {e}; register a valid STEP"
                ))
            })?;
            (part, analysis)
        } else {
            let name = Path::new(&registration.name);
            let (part, mut analysis) = if registration.features.is_some() {
                crate::model::analyse_step_part(name, &source, registration.part)?
            } else {
                crate::model::analyse_native_step_part(name, &source, registration.part)?
            };
            if let Some(features) = registration.features {
                crate::model::recognise(&part, &mut analysis, &features).map_err(Error::invalid)?;
            }
            atomic(&dir.join("analysis.json"), &encode(&analysis))?;
            (part, analysis)
        };
        let projections = if dir.join("projections.json").is_file() {
            Projections::from_disk(&fs::read(dir.join("projections.json")).map_err(io)?).map_err(
                |e| {
                    Error::failed(format!(
                        "invalid projection cache: {e}; remove projections.json and retry"
                    ))
                },
            )?
        } else {
            Projections::default()
        };
        #[cfg(test)]
        work::plan();
        Session::with_projections(part, analysis, spec.clone(), projections)
    }
    fn save(&self, dir: &Path, session: &Session) -> Result<(), Error> {
        // A disk-loaded cache whose entries did not change already has this exact
        // representation on disk. Avoid serializing and rereading all warm geometry.
        if !session.projections().dirty && dir.join("projections.json").is_file() {
            return Ok(());
        }
        let bytes = session
            .projections()
            .disk_bytes()
            .map_err(|e| Error::failed(e.to_string()))?;
        // Avoid rewriting warm artifacts on read-only queries.
        if fs::read(dir.join("projections.json")).ok().as_deref() != Some(&bytes) {
            atomic(&dir.join("projections.json"), &bytes)?;
        }
        Ok(())
    }
    pub fn session(&self, part_ref: &str, spec: &Spec) -> Result<Session, Error> {
        let dir = self.dir(part_ref)?;
        let _lock = Self::lock(&dir)?;
        let session = self.session_unlocked(&dir, spec)?;
        self.save(&dir, &session)?;
        Ok(session)
    }
    pub fn apply(&self, part_ref: &str, spec: &Spec, ops: &[Op]) -> Result<Applied, Error> {
        self.apply_with_refusal(part_ref, spec, ops, |_, error| error)
    }
    pub(crate) fn apply_with_refusal(
        &self,
        part_ref: &str,
        spec: &Spec,
        ops: &[Op],
        refused: impl FnOnce(&mut Session, Error) -> Error,
    ) -> Result<Applied, Error> {
        check_operation_count(ops.len())?;
        let dir = self.dir(part_ref)?;
        let _lock = Self::lock(&dir)?;
        let mut session = self.session_unlocked(&dir, spec)?;
        let delta =
            session_apply(&mut session, ops, true).map_err(|error| refused(&mut session, error))?;
        self.save(&dir, &session)?;
        Ok(Applied {
            spec: session.spec().clone(),
            delta,
        })
    }
    pub fn preview(&self, part_ref: &str, spec: &Spec, ops: &[Op]) -> Result<Delta, Error> {
        Ok(self.apply(part_ref, spec, ops)?.delta)
    }
    pub fn items(
        &self,
        part_ref: &str,
        spec: &Spec,
        filter: &ItemFilter,
    ) -> Result<Vec<ItemAnswer>, Error> {
        let session = self.session(part_ref, spec)?;
        if let Some(sheet) = filter.sheet {
            session.state().check_sheet(sheet)?;
        }
        if let Some(id) = &filter.near {
            session.item(id)?;
        }
        Ok(session.items(filter))
    }
    pub fn item(&self, part_ref: &str, spec: &Spec, id: &str) -> Result<ItemAnswer, Error> {
        self.session(part_ref, spec)?.item(id)
    }
    pub fn problems(
        &self,
        part_ref: &str,
        spec: &Spec,
        filter: &ProblemFilter,
    ) -> Result<Vec<crate::model::Problem>, Error> {
        Ok(self.session(part_ref, spec)?.problems(filter))
    }
    pub fn options(&self, part_ref: &str, spec: &Spec, id: &str) -> Result<Options, Error> {
        self.session(part_ref, spec)?.options(id)
    }
    pub fn summary(&self, part_ref: &str, spec: &Spec) -> Result<Value, Error> {
        let s = self.session(part_ref, spec)?;
        let mut items = s.items(&ItemFilter {
            kind: Some("dimension".into()),
            ..ItemFilter::default()
        });
        items.sort_by_key(|i| {
            (
                !crate::dims::is_envelope_extent(&i.item.id),
                !i.item.id.ends_with(".diameter"),
                i.item.id.clone(),
            )
        });
        let mut features = std::collections::BTreeSet::new();
        let mut keys = Vec::new();
        for item in &items {
            if !crate::dims::is_envelope_extent(&item.item.id)
                && !item.features.iter().any(|f| !features.contains(f))
            {
                continue;
            }
            let row = json!({"id":item.item.id,"label":item.item.label});
            keys.push(row);
            if encode(&keys).len() > 700 {
                keys.pop();
                continue;
            }
            features.extend(item.features.iter().cloned());
        }
        let mut summary = json!({"part_ref":part_ref,"part":s.analysis().binding.part,"face_count":s.analysis().binding.face_count,
            "envelope":s.analysis().envelope,"sheet":{"size":s.plan().sheet.size,"scale":s.plan().sheet.scale,"page":s.plan().sheet.page},
            "views":s.plan().views.iter().map(|v|&v.id).collect::<Vec<_>>(),"items":s.items(&ItemFilter::default()).len(),
            "status":crate::completeness::status(&s.state().problems),"completeness":s.plan().completeness,
            "key_items":keys,"problems":s.state().problems.len(),"spec_version":spec_hash(s.spec())});
        if !s.plan().sheets.is_empty() {
            summary["sheets"] = json!(s.plan().sheets.len() + 1);
            summary["sheet_details"] = json!(std::iter::once(&s.plan().sheet)
                .chain(&s.plan().sheets)
                .map(|sheet| json!({"number":sheet.number,"size":sheet.size,"scale":sheet.scale,"page":sheet.page}))
                .collect::<Vec<_>>());
        }
        Ok(summary)
    }

    pub fn render(
        &self,
        part_ref: &str,
        spec: &Spec,
        region: Option<[f64; 4]>,
        format: &str,
        overlay: bool,
        dpi: f64,
    ) -> Result<Vec<u8>, Error> {
        self.render_sheet(part_ref, spec, region, format, overlay, dpi, 1)
    }
    #[allow(clippy::too_many_arguments)]
    pub fn render_sheet(
        &self,
        part_ref: &str,
        spec: &Spec,
        region: Option<[f64; 4]>,
        format: &str,
        overlay: bool,
        dpi: f64,
        sheet: u32,
    ) -> Result<Vec<u8>, Error> {
        self.render_sheet_with_ids(part_ref, spec, region, format, overlay, dpi, sheet, None)
    }
    #[allow(clippy::too_many_arguments)]
    pub fn render_sheet_with_ids(
        &self,
        part_ref: &str,
        spec: &Spec,
        region: Option<[f64; 4]>,
        format: &str,
        overlay: bool,
        dpi: f64,
        sheet: u32,
        ids: Option<&[String]>,
    ) -> Result<Vec<u8>, Error> {
        let s = self.session(part_ref, spec)?;
        s.state().check_sheet(sheet)?;
        let selected = ids
            .map(|ids| {
                ids.iter()
                    .map(|id| {
                        let item = s.item(id)?;
                        if item.item.sheet != sheet {
                            return Err(Error {
                                choices: vec![format!(
                                    "sheet:{}/{}",
                                    item.item.sheet, item.item.id
                                )],
                                ..Error::invalid(format!(
                                    "{} is on sheet {}",
                                    item.item.id, item.item.sheet
                                ))
                            });
                        }
                        Ok(item)
                    })
                    .collect::<Result<Vec<_>, Error>>()
            })
            .transpose()?;
        let mut drawing = s.drawing_on_sheet(sheet)?;
        if overlay || selected.is_some() {
            for item in selected.unwrap_or_else(|| {
                s.items(&ItemFilter {
                    sheet: Some(sheet),
                    ..Default::default()
                })
            }) {
                if let Some(bounds) = crate::session::item_bounds(&item) {
                    let font = crate::text::Font::new(api::MONO.to_vec()).expect("bundled font");
                    let run = crate::output::TextRun {
                        text: item.item.id,
                        at: [bounds[0], bounds[3]],
                        size: 1.8,
                        rotation: 0.0,
                        font: "IBMPlexMono-Regular.ttf".into(),
                        h_align: crate::output::HAlign::Left,
                        v_align: crate::output::VAlign::Middle,
                    };
                    let laid = font.text(&run.text, run.size);
                    let at = [run.at[0] - laid.min[0], run.at[1] - laid.min[1]];
                    let point = |p: [f64; 2]| [p[0] + at[0], p[1] + at[1]];
                    let wires = laid
                        .contours
                        .iter()
                        .map(|c| {
                            c.iter()
                                .map(|s| match *s {
                                    crate::text::Segment::Line(a, b) => {
                                        crate::output::Curve::Line(point(a), point(b))
                                    }
                                    crate::text::Segment::Quad(a, b, c) => {
                                        crate::output::Curve::Beziers(vec![vec![
                                            point(a),
                                            point(b),
                                            point(c),
                                        ]])
                                    }
                                    crate::text::Segment::Cubic(a, b, c, d) => {
                                        crate::output::Curve::Beziers(vec![vec![
                                            point(a),
                                            point(b),
                                            point(c),
                                            point(d),
                                        ]])
                                    }
                                })
                                .collect()
                        })
                        .collect();
                    drawing.layers[0]
                        .items
                        .push(crate::output::Item::Face(wires));
                }
            }
        }
        match format {
            "png" => drawing.png_region(dpi, region).map_err(Error::invalid),
            "pdf" if region.is_none() => Ok(drawing.pdf(&|name| match name {
                "IBMPlexMono-Regular.ttf" => Some(api::MONO.to_vec()),
                crate::title_block::FONT => Some(crate::title_block::FONT_DATA.to_vec()),
                _ => None,
            })),
            _ => Err(Error {
                choices: vec!["png (optional region)".into(), "pdf (whole sheet)".into()],
                ..Error::invalid("choose PNG for a region or PDF for delivery")
            }),
        }
    }
    pub fn artifact(&self, bytes: &[u8], extension: &str) -> Result<String, Error> {
        let key = format!("{}.{}", hash(bytes), extension);
        let dir = self.root.join("artifacts");
        fs::create_dir_all(&dir).map_err(io)?;
        if fs::read(dir.join(&key)).ok().as_deref() != Some(bytes) {
            atomic(&dir.join(&key), bytes)?;
        }
        Ok(key)
    }
    pub fn artifact_bytes(&self, key: &str) -> Result<Vec<u8>, Error> {
        let Some((hash, ext)) = key.split_once('.') else {
            return Err(Error::invalid("use an artifact_ref returned by a tool"));
        };
        if hash.len() != 64
            || !hash.bytes().all(|b| b.is_ascii_hexdigit())
            || !["json", "png", "pdf"].contains(&ext)
        {
            return Err(Error::invalid("use an artifact_ref returned by a tool"));
        }
        fs::read(self.root.join("artifacts").join(key)).map_err(io)
    }
    pub fn version(&self, spec: &Spec) -> Result<String, Error> {
        let key = spec_hash(spec);
        let dir = self.root.join("specs");
        fs::create_dir_all(&dir).map_err(io)?;
        atomic(&dir.join(format!("{key}.json")), &encode(spec))?;
        Ok(key)
    }
    pub fn get_version(&self, key: &str) -> Result<Spec, Error> {
        if key.len() != 64 || !key.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err(Error::invalid(
                "spec_version is a SHA-256 returned by get_spec or apply_ops",
            ));
        }
        let path = self.root.join("specs").join(format!("{key}.json"));
        if !path.is_file() {
            return Err(Error::invalid(
                "unknown Spec version; call get_spec with part_ref and drawing_ref to obtain the current head, or pass a complete spec",
            ));
        }
        read(&path)
    }
    pub fn head(&self, part_ref: &str, drawing_ref: &str) -> Result<String, Error> {
        let dir = self.dir(part_ref)?;
        let _lock = Self::lock(&dir)?;
        self.head_unlocked(&dir, drawing_ref)
    }
    fn head_unlocked(&self, dir: &Path, drawing_ref: &str) -> Result<String, Error> {
        if !safe(drawing_ref) {
            return Err(Error::invalid(
                "drawing_ref accepts 1–160 ASCII letters, numbers, hyphens and underscores",
            ));
        }
        let path = dir.join(format!("head-{drawing_ref}.json"));
        if path.is_file() {
            read(&path)
        } else {
            let s = self.session_unlocked(dir, &Spec::default())?;
            self.save(dir, &s)?;
            let version = self.version(s.spec())?;
            atomic(&path, &encode(&version))?;
            Ok(version)
        }
    }
    pub fn apply_version(
        &self,
        part_ref: &str,
        drawing_ref: &str,
        parent: &str,
        ops: &[Op],
    ) -> Result<(String, Applied), Error> {
        self.apply_version_with_refusal(part_ref, drawing_ref, parent, ops, |_, error| error)
    }
    pub(crate) fn apply_version_with_refusal(
        &self,
        part_ref: &str,
        drawing_ref: &str,
        parent: &str,
        ops: &[Op],
        refused: impl FnOnce(&mut Session, Error) -> Error,
    ) -> Result<(String, Applied), Error> {
        check_operation_count(ops.len())?;
        let dir = self.dir(part_ref)?;
        let _lock = Self::lock(&dir)?;
        let head = self.head_unlocked(&dir, drawing_ref)?;
        if parent != head {
            return Err(Error {
                choices: vec![head.clone()],
                ..Error::invalid(format!(
                    "stale parent {parent}; current head is {head}; get_spec and retry ops against that version"
                ))
            });
        }
        let spec = self.get_version(parent)?;
        let mut s = self.session_unlocked(&dir, &spec)?;
        let delta = session_apply(&mut s, ops, true).map_err(|error| refused(&mut s, error))?;
        self.save(&dir, &s)?;
        let applied = Applied {
            spec: s.spec().clone(),
            delta,
        };
        let version = hash(&encode(
            &json!({"parent":parent,"spec":applied.spec,"ops":ops}),
        ));
        let spec_bytes = encode(&applied.spec);
        let delta_bytes = encode(&applied.delta);
        let edge_bytes = encode(
            &json!({"part_ref":part_ref,"drawing_ref":drawing_ref,"parent":parent,"version":version,"ops":ops,"delta":applied.delta}),
        );
        let edge = format!("{}.json", hash(&edge_bytes));
        // These immutable files are independent. Flush them concurrently, then publish
        // the head only after every write succeeds. A failure leaves the old head live;
        // a crash before publication can leave only unreachable immutable artifacts.
        std::thread::scope(|scope| {
            let writes = [
                scope.spawn(|| {
                    atomic(
                        &self.root.join("specs").join(format!("{version}.json")),
                        &spec_bytes,
                    )
                }),
                scope.spawn(|| self.artifact(&edge_bytes, "json").map(|_| ())),
                scope.spawn(|| self.artifact(&delta_bytes, "json").map(|_| ())),
                scope.spawn(|| {
                    atomic(
                        &dir.join(format!("edge-{drawing_ref}-{version}.json")),
                        &encode(&edge),
                    )
                }),
            ];
            for write in writes {
                write.join().map_err(|_| {
                    Error::failed("immutable history writer failed; retry the edit")
                })??;
            }
            Ok::<_, Error>(())
        })?;
        atomic(
            &dir.join(format!("head-{drawing_ref}.json")),
            &encode(&version),
        )?;
        Ok((version, applied))
    }
}
