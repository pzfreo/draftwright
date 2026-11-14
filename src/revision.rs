//! Revisions (ADR R3): a drawing's Spec carried from one revision of a part's STEP file to the
//! next, and what changed.
//!
//! quiddity matches the two parts (`quiddity::correspondence::correspond`): it aligns them and
//! classifies each recognised feature and face as carried, adapted, ambiguous, orphaned or new.
//! draftwright reads its own feature IDs through that match (a feature is the quiddity record with
//! the same faces, in the same file), then rebinds the Spec: every `f:<kind>:f<face>` and
//! `face:<n>` in an entry's key or values, and every face embedded in an ID
//! (`profile_angle.angle.f12`), names what it named before. quiddity does not match edges, so
//! an embedded edge (`:e3`) carries only when every face carried as it was. An entry is carried
//! (unchanged or renamed), or set aside, with its original kept in the report, never deleted:
//! *orphaned* (what it names is gone), *ambiguous* (the candidates are listed; none is guessed),
//! or refused by the new part ([`crate::session::Session::open_spec`]).
//!
//! The new revision is then drawn with the carried Spec and compared with the old drawing:
//! dimensions by ID (through the feature match), notes by ID, problems by code and subject, and
//! each view's visible edges in the part's own coordinates (so views that moved on the sheet
//! still compare). The comparison is drawn over the new drawing in colour ([`Revised::marked`]).

use crate::revision_rename::{next_id, rename_value};
use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::path::Path;

use haecceity::hlr::View;
use quiddity::correspondence::{self as qc, Class, Correspondence};
use serde::Serialize;
use serde_json::{Map, Value};

use crate::api::{self, Error, Features};
use crate::model::{self, Analysis, Spec};
use crate::output::{Curve, Drawing, Item, Layer, P};
use crate::session::Session;

/// Colours of the marked drawing: new, changed and old (removed or moved away from).
pub const ADDED: [u8; 3] = [10, 143, 60];
pub const CHANGED: [u8; 3] = [224, 122, 0];
pub const REMOVED: [u8; 3] = [214, 40, 40];

/// Two edges coincide within this distance on the sheet, mm.
const SAME: f64 = 0.1;

/// The old revision's drawing, the new one's drawn with the carried Spec, and what changed.
pub struct Revised {
    pub old: Session,
    pub new: Session,
    pub report: Report,
    /// quiddity's match of the two parts.
    pub correspondence: Correspondence,
    /// The edges only in one drawing, as counted in the report.
    edges: Edges,
}

#[derive(Clone, Debug, Serialize)]
pub struct Report {
    /// `ok`: carried in full, with no new problem; `decide`: a Spec entry was set aside or a
    /// match is ambiguous, so a person must decide; `problems`: the new drawing has problems the
    /// old one did not.
    pub verdict: String,
    pub summary: String,
    pub alignment: Alignment,
    pub features: Vec<FeatureChange>,
    pub spec: Vec<SpecEntry>,
    pub annotations: Vec<AnnotationChange>,
    pub problems: Problems,
    pub status: [crate::completeness::Status; 2],
    /// Stretches of visible edge drawn only in the new drawing, and only in the old one.
    pub edges: EdgeCounts,
    /// The Spec the new drawing was made from: the old one, carried.
    pub carried_spec: Value,
}

#[derive(Clone, Debug, Serialize)]
pub struct Alignment {
    pub found: bool,
    pub rotation: [[f64; 3]; 3],
    pub translation: [f64; 3],
}

/// A feature of either revision that did not carry unchanged, by draftwright's IDs.
#[derive(Clone, Debug, Serialize)]
pub struct FeatureChange {
    /// `adapted`, `ambiguous`, `orphaned` or `new`.
    pub class: String,
    pub kind: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub old: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub new: Option<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub candidates: Vec<String>,
    /// What changed, field by field, as quiddity reports it.
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub changes: BTreeMap<String, qc::Change>,
}

/// One entry of the Spec: its section and key (or a list member's ID or index).
#[derive(Clone, Debug, Serialize)]
pub struct SpecEntry {
    pub path: String,
    /// `renamed`, `orphaned`, `ambiguous`, `set_aside` (refused by the new part) or
    /// `refused_before` (the old part already refused it, so it says nothing of the revision).
    /// Entries carried unchanged are not listed.
    pub class: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub to: Option<String>,
    pub entry: Value,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub why: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub candidates: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct AnnotationChange {
    /// `added`, `removed` or `changed`.
    pub class: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub old: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub new: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub from: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub to: Option<String>,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct Problems {
    /// In the new drawing and not the old.
    pub added: Vec<ProblemRef>,
    /// In the old drawing and not the new.
    pub resolved: Vec<ProblemRef>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ProblemRef {
    pub tier: crate::finding::Tier,
    pub code: String,
    pub severity: String,
    pub message: String,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct EdgeCounts {
    pub added: usize,
    pub removed: usize,
    /// Views whose direction or up changed between the drawings: their edges are not compared.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub reoriented: Vec<String>,
}

/// What one old ID names in the new part.
#[derive(Clone, Debug, PartialEq)]
enum Bind {
    To(String),
    Orphaned,
    Ambiguous(Vec<String>),
}

/// Revise: draw *old* with *spec*, carry *spec* to *new*, draw it, and compare. *part* chooses a
/// part of an assembly in both files.
pub fn diff(old: &Path, new: &Path, part: Option<usize>, spec: &Value) -> Result<Revised, Error> {
    let (old_part, old_analysis) = api::analysed(old, part, Features::Native)?;
    let (new_part, new_analysis) = api::analysed(new, part, Features::Native)?;
    let old_q = qc::recognise(&old_part);
    let new_q = qc::recognise(&new_part);
    let corr = qc::correspond(&old_q.fingerprints, &new_q.fingerprints).map_err(Error::failed)?;
    let matching = Matching::new(
        &corr,
        &old_analysis,
        &new_analysis,
        &old_q.fingerprints,
        &new_q.fingerprints,
    );

    let json = |v: &Value| serde_json::to_string(v).expect("JSON serialises");
    let mut old_session = Session::new(old_part, old_analysis, Spec::default())?;
    let old_opened = old_session.open_spec(&json(spec))?;
    let mut entries = Vec::new();
    for s in &old_opened.set_aside {
        entries.push(SpecEntry {
            path: unescape(&s.path),
            class: "refused_before".into(),
            to: None,
            entry: s.entry.clone(),
            why: format!("already refused by the old revision ({})", s.reason_code),
            candidates: Vec::new(),
        });
    }
    let (carried, mut rebound) = matching.carry(&old_opened.spec);
    entries.append(&mut rebound);
    let mut new_session = Session::new(new_part, new_analysis, Spec::default())?;
    let new_opened = new_session.open_spec(&json(&carried))?;
    for s in &new_opened.set_aside {
        entries.push(SpecEntry {
            path: unescape(&s.path),
            class: "set_aside".into(),
            to: None,
            entry: s.entry.clone(),
            why: format!("the new revision refuses it ({})", s.reason_code),
            candidates: Vec::new(),
        });
    }

    let annotations = annotations(&old_session, &new_session, &matching);
    let problems = problems(&old_session, &new_session, &matching);
    let edges = compare_edges(&old_session, &new_session, &corr);
    let features = matching.changes(&corr, &old_session, &new_session);
    let status = [old_session.plan().status, new_session.plan().status];
    let open = entries.iter().filter(|e| e.needs_decision()).count()
        + features.iter().filter(|f| f.class == "ambiguous").count();
    let verdict = if !problems.added.is_empty() {
        "problems"
    } else if open > 0 {
        "decide"
    } else {
        "ok"
    };
    let mut summary = match verdict {
        "ok" => "The drawing rebuilds from the carried Spec with no new problems.".to_string(),
        "decide" => format!(
            "The drawing rebuilds, but {open} {} a decision.",
            if open == 1 {
                "Spec entry or match needs"
            } else {
                "Spec entries or matches need"
            }
        ),
        _ => format!(
            "The new drawing has {} problem{} the old one did not.",
            problems.added.len(),
            if problems.added.len() == 1 { "" } else { "s" }
        ),
    };
    if turned(&corr) {
        summary += " The whole part is turned relative to the old file, so its views changed: \
                    dimensions along each axis are compared as drawn, and edges are not compared.";
    }
    let counts = edges.counts();
    if !counts.reoriented.is_empty() {
        summary += &format!(
            " The {} view{} now look{} along another direction, so {} edges are not compared.",
            counts.reoriented.join(", "),
            if counts.reoriented.len() == 1 {
                ""
            } else {
                "s"
            },
            if counts.reoriented.len() == 1 {
                "s"
            } else {
                ""
            },
            if counts.reoriented.len() == 1 {
                "its"
            } else {
                "their"
            },
        );
    }
    Ok(Revised {
        report: Report {
            verdict: verdict.into(),
            summary,
            alignment: Alignment {
                found: corr.alignment.found,
                rotation: corr.alignment.rotation,
                translation: corr.alignment.translation,
            },
            features,
            spec: entries,
            annotations,
            problems,
            status,
            edges: counts,
            carried_spec: new_opened.spec,
        },
        old: old_session,
        new: new_session,
        correspondence: corr,
        edges,
    })
}

impl SpecEntry {
    /// Whether a person must decide on this entry: it did not carry because of the revision.
    fn needs_decision(&self) -> bool {
        !matches!(self.class.as_str(), "renamed" | "refused_before")
    }
}

/// The two parts' draftwright features and faces, matched through quiddity's correspondence.
struct Matching {
    features: BTreeMap<String, Bind>,
    faces: BTreeMap<usize, Bind>,
    /// Each old draftwright feature's quiddity record.
    records: BTreeMap<String, String>,
    /// Every face carried with its number and nothing is new, so edge numbers still hold.
    same_faces: bool,
}

/// Each draftwright feature's quiddity record: the one sharing most of its faces.
fn records(analysis: &Analysis, prints: &qc::Fingerprints) -> BTreeMap<String, String> {
    analysis
        .features
        .iter()
        .filter_map(|f| {
            let best = prints
                .features
                .iter()
                .map(|r| (r, r.faces.iter().filter(|i| f.faces.contains(i)).count()))
                .filter(|(_, n)| *n > 0)
                .max_by_key(|(r, n)| (*n, std::cmp::Reverse(r.faces.len())))?;
            Some((f.id.clone(), best.0.id.clone()))
        })
        .collect()
}

impl Matching {
    fn new(
        corr: &Correspondence,
        old: &Analysis,
        new: &Analysis,
        old_prints: &qc::Fingerprints,
        new_prints: &qc::Fingerprints,
    ) -> Self {
        let old_records = records(old, old_prints);
        // Several draftwright features can share a record (a countersink and its hole): a new
        // feature is the one on the matched record that is of the old feature's kind, or the
        // record's only feature (a hole that gained a counterbore changes kind).
        let kind = |a: &Analysis, id: &str| {
            a.features
                .iter()
                .find(|f| f.id == id)
                .map(|f| f.kind.clone())
        };
        let mut by_record: BTreeMap<String, Vec<(String, Option<String>)>> = BTreeMap::new();
        for (feature, record) in records(new, new_prints) {
            let k = kind(new, &feature);
            by_record.entry(record).or_default().push((feature, k));
        }
        let entries: BTreeMap<&str, &qc::Entry<String>> =
            corr.features.iter().map(|e| (e.old.as_str(), e)).collect();
        // Where one record carries several features of a kind (a pocket's floor rounds, each its
        // own fillet), the one sharing faces with the old feature, as the faces carried, is it.
        let carried: BTreeMap<usize, usize> = corr
            .faces
            .iter()
            .filter(|e| matches!(e.class, Class::Carried | Class::Adapted))
            .filter_map(|e| Some((e.old, e.new?)))
            .collect();
        let to_new = |record: &String, k: &str, old_faces: &[usize]| {
            by_record.get(record).and_then(|on| {
                let same: Vec<&(String, Option<String>)> = on
                    .iter()
                    .filter(|(_, nk)| nk.as_deref() == Some(k))
                    .collect();
                if same.len() > 1 {
                    let now: Vec<usize> = old_faces
                        .iter()
                        .filter_map(|f| carried.get(f).copied())
                        .collect();
                    let sharing: Vec<_> = same
                        .iter()
                        .filter(|(f, _)| {
                            new.features
                                .iter()
                                .find(|g| g.id == *f)
                                .is_some_and(|g| g.faces.iter().any(|i| now.contains(i)))
                        })
                        .collect();
                    if let [one] = sharing[..] {
                        return Some(one.0.clone());
                    }
                }
                same.first()
                    .copied()
                    .or(if on.len() == 1 { on.first() } else { None })
                    .map(|(f, _)| f.clone())
            })
        };
        let mut features: BTreeMap<String, Bind> = old
            .features
            .iter()
            .map(|f| {
                let bind = match old_records.get(&f.id).and_then(|r| entries.get(r.as_str())) {
                    Some(e) => match e.class {
                        Class::Carried | Class::Adapted => e
                            .new
                            .as_ref()
                            .and_then(|r| to_new(r, &f.kind, &f.faces))
                            .map_or(Bind::Orphaned, Bind::To),
                        Class::Ambiguous => Bind::Ambiguous(
                            e.candidates
                                .iter()
                                .filter_map(|r| to_new(r, &f.kind, &f.faces))
                                .collect(),
                        ),
                        _ => Bind::Orphaned,
                    },
                    None => Bind::Orphaned,
                };
                (f.id.clone(), bind)
            })
            .collect();
        // Two old features that still reach one new feature can't both be it: neither is
        // guessed.
        let mut reached: BTreeMap<String, usize> = BTreeMap::new();
        for b in features.values() {
            if let Bind::To(n) = b {
                *reached.entry(n.clone()).or_default() += 1;
            }
        }
        for b in features.values_mut() {
            if let Bind::To(n) = b
                && reached[n.as_str()] > 1
            {
                *b = Bind::Ambiguous(vec![n.clone()]);
            }
        }
        let faces = corr
            .faces
            .iter()
            .map(|e| {
                let bind = match e.class {
                    Class::Carried | Class::Adapted => e
                        .new
                        .map_or(Bind::Orphaned, |n| Bind::To(format!("face:{n}"))),
                    Class::Ambiguous => {
                        Bind::Ambiguous(e.candidates.iter().map(|n| format!("face:{n}")).collect())
                    }
                    _ => Bind::Orphaned,
                };
                (e.old, bind)
            })
            .collect();
        let same_faces = corr.new_faces.is_empty()
            && corr
                .faces
                .iter()
                .all(|e| e.class == Class::Carried && e.new == Some(e.old));
        Matching {
            features,
            faces,
            records: old_records,
            same_faces,
        }
    }

    /// What an old ID (`f:<kind>:f<face>`, `face:<n>`, or an embedded `f<n>.<m>` or `e<n>`) names
    /// now; an ID the old part did not have (one the Spec declares itself) is left as it is.
    fn bind(&self, id: &str) -> Bind {
        let numbered = |l: &&str| l.starts_with(|c: char| c.is_ascii_digit());
        if id.strip_prefix('e').filter(numbered).is_some() {
            // quiddity matches faces, not edges: an edge number means the same edge only when
            // every face carried as it was.
            return if self.same_faces {
                Bind::To(id.into())
            } else {
                Bind::Orphaned
            };
        }
        if let Some(list) = id.strip_prefix('f').filter(numbered) {
            let mut now = Vec::new();
            for n in list.split('.') {
                match self.bind(&format!("face:{n}")) {
                    Bind::To(m) => now.push(m.trim_start_matches("face:").to_string()),
                    Bind::Ambiguous(c) => {
                        return Bind::Ambiguous(
                            c.iter()
                                .map(|m| format!("f{}", m.trim_start_matches("face:")))
                                .collect(),
                        );
                    }
                    Bind::Orphaned => return Bind::Orphaned,
                }
            }
            return Bind::To(format!("f{}", now.join(".")));
        }
        if let Some(n) = id
            .strip_prefix("face:")
            .and_then(|n| n.parse::<usize>().ok())
        {
            return self.faces.get(&n).cloned().unwrap_or(Bind::To(id.into()));
        }
        self.features
            .get(id)
            .cloned()
            .unwrap_or(Bind::To(id.into()))
    }

    /// *s* with each ID renamed, or the first ID that does not carry.
    fn rename(&self, s: &str) -> Result<String, (String, Bind)> {
        let mut out = String::with_capacity(s.len());
        let mut rest = s;
        while let Some((start, end)) = next_id(rest) {
            out.push_str(&rest[..start]);
            let id = &rest[start..end];
            match self.bind(id) {
                Bind::To(now) => out.push_str(&now),
                other => return Err((id.into(), other)),
            }
            rest = &rest[end..];
        }
        out.push_str(rest);
        Ok(out)
    }

    fn rename_value(&self, v: &Value) -> Result<Value, (String, Bind)> {
        rename_value(v, &|s| self.rename(s), &|s| {
            (s, Bind::Ambiguous(Vec::new()))
        })
    }

    /// The Spec with every entry rebound, and the entries that were renamed or set aside.
    fn carry(&self, spec: &Value) -> (Value, Vec<SpecEntry>) {
        let mut out = Map::new();
        let mut report = Vec::new();
        let Some(sections) = spec.as_object() else {
            return (spec.clone(), report);
        };
        for (section, value) in sections {
            match value {
                _ if section == "binding" => {}
                // The sheet's settings are carried one by one: an ID that does not carry sets
                // aside only the setting that names it.
                Value::Object(_) if section == "sheet" => {
                    if let Some(v) = self.carry_part(section, value, true, &mut report) {
                        out.insert(section.clone(), v);
                    }
                }
                Value::Object(entries) => {
                    let results: Vec<_> = entries
                        .iter()
                        .map(|(key, entry)| {
                            let result = self
                                .rename(key)
                                .and_then(|k| Ok((k, self.rename_value(entry)?)));
                            (key, entry, result)
                        })
                        .collect();
                    let mut reached: BTreeMap<&str, usize> = BTreeMap::new();
                    for (_, _, r) in &results {
                        if let Ok((k, _)) = r {
                            *reached.entry(k.as_str()).or_default() += 1;
                        }
                    }
                    let mut kept = Map::new();
                    for (key, entry, result) in &results {
                        let path = format!("{section}/{key}");
                        match result {
                            Err(e) => report.push(set_aside(path, entry, e)),
                            // Two entries would carry to one key: neither is chosen.
                            Ok((to, _)) if reached[to.as_str()] > 1 => report.push(SpecEntry {
                                path,
                                class: "ambiguous".into(),
                                to: None,
                                entry: (*entry).clone(),
                                why: format!("more than one entry carries to {to}"),
                                candidates: vec![to.clone()],
                            }),
                            Ok((to, v)) => {
                                if to != *key || v != *entry {
                                    report.push(renamed(path, entry, to.clone()));
                                }
                                kept.insert(to.clone(), v.clone());
                            }
                        }
                    }
                    out.insert(section.clone(), Value::Object(kept));
                }
                Value::Array(list) => {
                    let mut kept = Vec::new();
                    for (i, entry) in list.iter().enumerate() {
                        let name = entry
                            .get("id")
                            .and_then(Value::as_str)
                            .map_or(i.to_string(), String::from);
                        if let Some(v) =
                            self.carry_part(&format!("{section}/{name}"), entry, false, &mut report)
                        {
                            kept.push(v);
                        }
                    }
                    out.insert(section.clone(), Value::Array(kept));
                }
                other => {
                    if let Some(v) = self.carry_part(section, other, false, &mut report) {
                        out.insert(section.clone(), v);
                    }
                }
            }
        }
        (Value::Object(out), report)
    }

    /// *value* (at *path*) renamed, or set aside. With *partial* (the sheet), an object or list
    /// that does not carry is carried member by member, each one that does not set aside alone.
    fn carry_part(
        &self,
        path: &str,
        value: &Value,
        partial: bool,
        report: &mut Vec<SpecEntry>,
    ) -> Option<Value> {
        let e = match self.rename_value(value) {
            Ok(v) => {
                if v != *value {
                    report.push(renamed(path.into(), value, String::new()));
                }
                return Some(v);
            }
            Err(e) => e,
        };
        match value {
            Value::Object(o) if partial => Some(Value::Object(
                o.iter()
                    .filter_map(|(k, x)| {
                        let at = format!("{path}/{k}");
                        let k = match self.rename(k) {
                            Ok(k) => k,
                            Err(e) => {
                                report.push(set_aside(at, x, &e));
                                return None;
                            }
                        };
                        Some((k, self.carry_part(&at, x, true, report)?))
                    })
                    .collect(),
            )),
            Value::Array(a) if partial => Some(Value::Array(
                a.iter()
                    .enumerate()
                    .filter_map(|(i, x)| self.carry_part(&format!("{path}/{i}"), x, true, report))
                    .collect(),
            )),
            _ => {
                report.push(set_aside(path.into(), value, &e));
                None
            }
        }
    }

    /// The new ID of an old drawing item's ID (`d:f:hole:3/…`), if its feature carried.
    fn item(&self, id: &str) -> Option<String> {
        self.rename(id).ok()
    }

    /// Each feature that did not carry unchanged, by draftwright's IDs; quiddity records that are
    /// no draftwright feature (evidence, such as risers) are left out.
    fn changes(&self, corr: &Correspondence, old: &Session, new: &Session) -> Vec<FeatureChange> {
        let kind = |a: &Analysis, id: &str| {
            a.features
                .iter()
                .find(|f| f.id == id)
                .map(|f| f.kind.clone())
        };
        let mut out = Vec::new();
        let entries: BTreeMap<&str, &qc::Entry<String>> =
            corr.features.iter().map(|e| (e.old.as_str(), e)).collect();
        for (feature, bind) in &self.features {
            let entry = self
                .records
                .get(feature)
                .and_then(|r| entries.get(r.as_str()));
            let (class, newer, candidates) = match bind {
                Bind::To(n) if entry.is_some_and(|e| e.class == Class::Carried) => continue,
                Bind::To(n) => ("adapted", Some(n.clone()), Vec::new()),
                Bind::Ambiguous(c) => ("ambiguous", None, c.clone()),
                Bind::Orphaned => ("orphaned", None, Vec::new()),
            };
            if class == "adapted" && entry.is_none_or(|e| e.changes.is_empty()) {
                continue;
            }
            out.push(FeatureChange {
                class: class.into(),
                kind: kind(old.analysis(), feature).unwrap_or_default(),
                old: Some(feature.clone()),
                new: newer,
                candidates,
                changes: entry.map(|e| e.changes.clone()).unwrap_or_default(),
            });
        }
        let reached: HashSet<String> = self
            .features
            .values()
            .flat_map(|b| match b {
                Bind::To(n) => vec![n.clone()],
                Bind::Ambiguous(c) => c.clone(),
                Bind::Orphaned => vec![],
            })
            .collect();
        for f in &new.analysis().features {
            if !reached.contains(&f.id) {
                out.push(FeatureChange {
                    class: "new".into(),
                    kind: f.kind.clone(),
                    old: None,
                    new: Some(f.id.clone()),
                    candidates: Vec::new(),
                    changes: BTreeMap::new(),
                });
            }
        }
        out
    }
}

/// A JSON pointer's path as written: `~1` is `/`, `~0` is `~`.
fn unescape(path: &str) -> String {
    path.replace("~1", "/").replace("~0", "~")
}

/// A Spec entry carried under a new name, or with renamed references (*to* empty).
fn renamed(path: String, entry: &Value, to: String) -> SpecEntry {
    SpecEntry {
        path,
        class: "renamed".into(),
        to: (!to.is_empty()).then_some(to),
        entry: entry.clone(),
        why: String::new(),
        candidates: Vec::new(),
    }
}

/// A Spec entry set aside because *id* does not carry.
fn set_aside(path: String, entry: &Value, (id, bind): &(String, Bind)) -> SpecEntry {
    let (class, why, candidates) = match bind {
        Bind::Ambiguous(c) => (
            "ambiguous",
            format!("{id} matches more than one feature of the new part"),
            c.clone(),
        ),
        _ => (
            "orphaned",
            format!("{id} has no match in the new part"),
            Vec::new(),
        ),
    };
    SpecEntry {
        path,
        class: class.into(),
        to: None,
        entry: entry.clone(),
        why,
        candidates,
    }
}

fn annotations(old: &Session, new: &Session, m: &Matching) -> Vec<AnnotationChange> {
    let placed = |s: &Session| -> BTreeMap<String, String> {
        s.plan()
            .dimensions
            .iter()
            .filter(|d| d.placed.is_some())
            .map(|d| (d.id.clone(), d.label.clone()))
            .collect()
    };
    let (before, after) = (placed(old), placed(new));
    let mut out = Vec::new();
    let mut seen = BTreeSet::new();
    for (id, label) in &before {
        match m.item(id).filter(|n| after.contains_key(n)) {
            Some(now) => {
                seen.insert(now.clone());
                if after[&now] != *label {
                    out.push(AnnotationChange {
                        class: "changed".into(),
                        old: Some(id.clone()),
                        new: Some(now.clone()),
                        from: Some(label.clone()),
                        to: Some(after[&now].clone()),
                    });
                }
            }
            None => out.push(AnnotationChange {
                class: "removed".into(),
                old: Some(id.clone()),
                new: None,
                from: Some(label.clone()),
                to: None,
            }),
        }
    }
    for (id, label) in &after {
        if !seen.contains(id) {
            out.push(AnnotationChange {
                class: "added".into(),
                old: None,
                new: Some(id.clone()),
                from: None,
                to: Some(label.clone()),
            });
        }
    }
    let notes = |s: &Session| -> BTreeMap<String, String> {
        s.plan()
            .notes
            .iter()
            .map(|n| (n.id.clone(), n.text.clone()))
            .collect()
    };
    // Notes are matched as dimensions are: an automatic note's ID names its feature.
    let after = notes(new);
    let before: BTreeMap<String, (String, String)> = notes(old)
        .into_iter()
        .map(|(id, text)| {
            let now = m
                .item(&id)
                .filter(|n| after.contains_key(n))
                .unwrap_or_else(|| format!("\u{0}gone {id}"));
            (now, (id, text))
        })
        .collect();
    for (id, text) in &after {
        match before.get(id) {
            Some((_, was)) if was == text => {}
            Some((was_id, was)) => out.push(AnnotationChange {
                class: "changed".into(),
                old: Some(was_id.clone()),
                new: Some(id.clone()),
                from: Some(was.clone()),
                to: Some(text.clone()),
            }),
            None => out.push(AnnotationChange {
                class: "added".into(),
                old: None,
                new: Some(id.clone()),
                from: None,
                to: Some(text.clone()),
            }),
        }
    }
    for (now, (id, text)) in &before {
        if !after.contains_key(now) {
            out.push(AnnotationChange {
                class: "removed".into(),
                old: Some(id.clone()),
                new: None,
                from: Some(text.clone()),
                to: None,
            });
        }
    }
    out
}

fn problems(old: &Session, new: &Session, m: &Matching) -> Problems {
    let key = |p: &model::Problem, rename: bool| {
        let subjects: Vec<String> = p
            .subjects
            .iter()
            .map(|s| {
                if rename {
                    // An old subject with no match names nothing new: it can't match any new
                    // problem, even one whose subject now has its old number.
                    m.item(s).unwrap_or_else(|| format!("\u{0}gone {s}"))
                } else {
                    s.clone()
                }
            })
            .collect();
        (p.code.clone(), subjects)
    };
    let before: BTreeSet<_> = old.state().problems.iter().map(|p| key(p, true)).collect();
    let after: BTreeSet<_> = new.state().problems.iter().map(|p| key(p, false)).collect();
    let r = |p: &model::Problem| ProblemRef {
        tier: p.labels().tier,
        code: p.code.clone(),
        severity: p.severity.clone(),
        message: p.message.clone(),
    };
    Problems {
        added: new
            .state()
            .problems
            .iter()
            .filter(|p| p.severity != "info" && !before.contains(&key(p, false)))
            .map(r)
            .collect(),
        resolved: old
            .state()
            .problems
            .iter()
            .filter(|p| p.severity != "info" && !after.contains(&key(p, true)))
            .map(r)
            .collect(),
    }
}

/// Visible edges, sampled, on the new sheet: those only in the new drawing and those only in the
/// old one (carried onto the new sheet through each view's frame).
struct Edges {
    added: Vec<Vec<P>>,
    removed: Vec<Vec<P>>,
    reoriented: Vec<String>,
}

impl Edges {
    fn counts(&self) -> EdgeCounts {
        EdgeCounts {
            added: self.added.len(),
            removed: self.removed.len(),
            reoriented: self.reoriented.clone(),
        }
    }
}

fn sample(c: &Curve) -> Vec<P> {
    let line = |a: P, b: P| -> Vec<P> {
        let n = ((b[0] - a[0]).hypot(b[1] - a[1]) / (SAME * 0.5))
            .ceil()
            .max(1.0) as usize;
        (0..=n)
            .map(|k| {
                let t = k as f64 / n as f64;
                [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
            })
            .collect()
    };
    let bezier = |p: &[P], t: f64| -> P {
        let mut q = p.to_vec();
        for r in 1..q.len() {
            for i in 0..q.len() - r {
                q[i] = [
                    q[i][0] + (q[i + 1][0] - q[i][0]) * t,
                    q[i][1] + (q[i + 1][1] - q[i][1]) * t,
                ];
            }
        }
        q[0]
    };
    let dense = |pts: Vec<P>| -> Vec<P> { pts.windows(2).flat_map(|w| line(w[0], w[1])).collect() };
    match c {
        Curve::Line(a, b) => line(*a, *b),
        Curve::Conic {
            centre,
            radii,
            x_axis,
            y_axis,
            t0,
            t1,
        } => dense(
            (0..=64)
                .map(|k| {
                    let t = t0 + (t1 - t0) * k as f64 / 64.0;
                    let (cs, sn) = (t.cos() * radii[0], t.sin() * radii[1]);
                    [
                        centre[0] + cs * x_axis[0] + sn * y_axis[0],
                        centre[1] + cs * x_axis[1] + sn * y_axis[1],
                    ]
                })
                .collect(),
        ),
        Curve::Beziers(pieces) => dense(
            pieces
                .iter()
                .flat_map(|p| (0..=16).map(move |k| bezier(p, k as f64 / 16.0)))
                .collect(),
        ),
        Curve::Rational(pieces) => dense(
            pieces
                .iter()
                .flat_map(|p| {
                    (0..=16).map(move |k| {
                        let mut q = p.clone();
                        let t = k as f64 / 16.0;
                        for r in 1..q.len() {
                            for i in 0..q.len() - r {
                                let next = q[i + 1];
                                for (x, n) in q[i].iter_mut().zip(next) {
                                    *x += (n - *x) * t;
                                }
                            }
                        }
                        [q[0][0] / q[0][2], q[0][1] / q[0][2]]
                    })
                })
                .collect(),
        ),
    }
}

fn cell(p: P) -> (i64, i64) {
    ((p[0] / SAME).round() as i64, (p[1] / SAME).round() as i64)
}

/// The stretches of a view's sampled edges that the other drawing has no edge near, longer than
/// 0.5 mm once joined end to end (a traced silhouette comes as many short pieces).
fn uncovered(edges: &[Vec<P>], grid: &HashSet<(i64, i64)>) -> Vec<Vec<P>> {
    let near = |p: &P| {
        let (x, y) = cell(*p);
        (-1..=1).any(|dx| (-1..=1).any(|dy| grid.contains(&(x + dx, y + dy))))
    };
    let length = |run: &[P]| {
        run.windows(2)
            .map(|w| (w[1][0] - w[0][0]).hypot(w[1][1] - w[0][1]))
            .sum::<f64>()
    };
    let mut runs: Vec<Vec<P>> = Vec::new();
    for run in edges
        .iter()
        .flat_map(|e| e.split(near))
        .filter(|r| r.len() > 1)
    {
        match runs.last_mut() {
            Some(last)
                if {
                    let (a, b) = (last[last.len() - 1], run[0]);
                    (a[0] - b[0]).hypot(a[1] - b[1]) < SAME
                } =>
            {
                last.extend_from_slice(&run[1..])
            }
            _ => runs.push(run.to_vec()),
        }
    }
    runs.retain(|r| length(r) > 0.5);
    runs
}

/// Whether quiddity aligned the parts by a turn, not a translation alone.
fn turned(corr: &Correspondence) -> bool {
    let r = corr.alignment.rotation;
    corr.alignment.found
        && (0..3).any(|i| (0..3).any(|j| (r[i][j] - if i == j { 1.0 } else { 0.0 }).abs() > 1e-6))
}

fn compare_edges(old: &Session, new: &Session, corr: &Correspondence) -> Edges {
    let mut edges = Edges {
        added: Vec::new(),
        removed: Vec::new(),
        reoriented: Vec::new(),
    };
    if turned(corr) {
        // A turned revision's views are not the same projection: compare annotations only.
        return edges;
    }
    let shift = if corr.alignment.found {
        corr.alignment.translation
    } else {
        [0.0; 3]
    };
    let (od, nd) = (old.drawing(), new.drawing());
    for view in &nd.sheet.views {
        let key = view.name.trim_start_matches("v:");
        let (Some(of), Some(nf), Some(ov)) = (
            od.sheet.frames.get(key),
            nd.sheet.frames.get(key),
            od.sheet.views.iter().find(|v| v.name == view.name),
        ) else {
            continue;
        };
        let same = |a: [f64; 3], b: [f64; 3]| (0..3).all(|k| (a[k] - b[k]).abs() < 1e-9);
        if !same(of.direction, nf.direction) || !same(of.up, nf.up) {
            // The planner turned this view: its old edges are another projection.
            edges.reoriented.push(key.to_string());
            continue;
        }
        let Some(camera) = View::new(nf.direction, nf.up) else {
            continue;
        };
        let oc = model::envelope_extent(old.analysis().envelope, &camera).1;
        let nc = model::envelope_extent(new.analysis().envelope, &camera).1;
        let moved = camera.map(shift);
        // Old sheet → the old part's view coordinates → the new part's → the new sheet.
        let carry = |p: P| -> P {
            [0, 1].map(|k| {
                nf.at[k] + nf.scale * ((p[k] - of.at[k]) / of.scale + oc[k] + moved[k] - nc[k])
            })
        };
        let before: Vec<Vec<P>> = ov
            .edges
            .iter()
            .map(|c| sample(c).into_iter().map(carry).collect())
            .collect();
        let after: Vec<Vec<P>> = view.edges.iter().map(sample).collect();
        let grid = |set: &[Vec<P>]| -> HashSet<(i64, i64)> {
            set.iter().flatten().map(|p| cell(*p)).collect()
        };
        let (gb, ga) = (grid(&before), grid(&after));
        edges.added.extend(uncovered(&after, &gb));
        edges.removed.extend(uncovered(&before, &ga));
    }
    edges
}

impl Revised {
    /// The new drawing with what changed drawn over it: new edges and new or changed annotations
    /// in green and orange, the old drawing's edges that are gone in red, notes boxed.
    pub fn marked(&self) -> Drawing {
        let mut d = self.new.drawing().clone();
        let base = d
            .layers
            .iter()
            .find(|l| l.name == "part")
            .map(|l| l.style.clone())
            .expect("a drawing has a part layer");
        let layer = |name: &str, colour: [u8; 3], items: Vec<Item>| Layer {
            name: name.into(),
            style: crate::output::Style {
                colour,
                width: 0.6,
                dash: None,
                fill: false,
                ..base.clone()
            },
            items,
        };
        let edges = &self.edges;
        let polyline = |p: &Vec<P>| {
            Item::Edge(Curve::Beziers(
                p.windows(2).map(|w| vec![w[0], w[1]]).collect(),
            ))
        };
        let rect = |b: [f64; 4]| {
            let [x0, y0, x1, y1] = [b[0] - 0.8, b[1] - 0.8, b[2] + 0.8, b[3] + 0.8];
            Item::Edge(Curve::Beziers(vec![
                vec![[x0, y0], [x1, y0]],
                vec![[x1, y0], [x1, y1]],
                vec![[x1, y1], [x0, y1]],
                vec![[x0, y1], [x0, y0]],
            ]))
        };
        let mut marks: BTreeMap<&str, Vec<Item>> = BTreeMap::new();
        for a in &self.report.annotations {
            let (Some(id), class) = (&a.new, a.class.as_str()) else {
                continue;
            };
            if class == "removed" {
                continue;
            }
            let into = marks
                .entry(if class == "added" { "added" } else { "changed" })
                .or_default();
            if let Some(drawn) = d.sheet.items.iter().find(|i| &i.name == id) {
                // The label alone: its extension lines would cross the whole view.
                into.push(rect(drawn.label.unwrap_or(drawn.ink)));
            } else if let Some(cells) = self
                .new
                .plan()
                .notes
                .iter()
                .find(|n| &n.id == id)
                .and_then(|n| n.placed.as_ref())
                .map(|p| p.cells)
            {
                into.push(rect(cells));
            }
        }
        d.layers.push(layer(
            "revision_removed",
            REMOVED,
            edges.removed.iter().map(polyline).collect(),
        ));
        d.layers.push(layer(
            "revision_added",
            ADDED,
            edges
                .added
                .iter()
                .map(polyline)
                .chain(marks.remove("added").unwrap_or_default())
                .collect(),
        ));
        d.layers.push(layer(
            "revision_changed",
            CHANGED,
            marks.remove("changed").unwrap_or_default(),
        ));
        d
    }

    /// The report as Markdown, for a pull request comment; *image* is the marked drawing's link.
    pub fn markdown(&self, title: &str, image: Option<&str>) -> String {
        use std::fmt::Write;
        let r = &self.report;
        let mark = match r.verdict.as_str() {
            "ok" => "✅",
            "decide" => "⚠️",
            _ => "❌",
        };
        let mut s = format!("### {title}\n\n{mark} {}\n\n", r.summary);
        if let Some(image) = image {
            let _ = writeln!(
                s,
                "![What changed: new in green, changed in orange, gone in red]({image})\n"
            );
        }
        let fmt = |v: &Value| match v {
            Value::String(s) => s.clone(),
            other => other.to_string(),
        };
        if !r.features.is_empty() {
            s += "**The part**\n\n";
            for f in &r.features {
                let who = f
                    .old
                    .as_ref()
                    .or(f.new.as_ref())
                    .cloned()
                    .unwrap_or_default();
                let changes: Vec<String> = f
                    .changes
                    .iter()
                    .map(|(k, c)| format!("{k} {} → {}", fmt(&c.old), fmt(&c.new)))
                    .collect();
                let line = match f.class.as_str() {
                    "adapted" => format!(
                        "- {} {who}{}: {}",
                        f.kind,
                        f.new
                            .as_ref()
                            .filter(|n| **n != who)
                            .map_or(String::new(), |n| format!(" (now {n})")),
                        changes.join("; ")
                    ),
                    "ambiguous" => format!(
                        "- {} {who}: can't tell which it is now ({})",
                        f.kind,
                        f.candidates.join(", ")
                    ),
                    "orphaned" => format!("- {} {who} is gone", f.kind),
                    _ => format!("- new {} {who}", f.kind),
                };
                let _ = writeln!(s, "{line}");
            }
            s += "\n";
        }
        if !r.annotations.is_empty() {
            s += "**The drawing**\n\n";
            for a in &r.annotations {
                let id = a
                    .new
                    .as_ref()
                    .or(a.old.as_ref())
                    .cloned()
                    .unwrap_or_default();
                let _ = writeln!(
                    s,
                    "- {} `{id}`: {}",
                    a.class,
                    match (&a.from, &a.to) {
                        (Some(f), Some(t)) => format!("{f} → {t}"),
                        (Some(f), None) => f.clone(),
                        (None, Some(t)) => t.clone(),
                        _ => String::new(),
                    }
                );
            }
            s += "\n";
        }
        let decisions: Vec<&SpecEntry> = r.spec.iter().filter(|e| e.needs_decision()).collect();
        if !decisions.is_empty() {
            s += "**Spec entries that need a decision**\n\n";
            for e in decisions {
                let _ = writeln!(
                    s,
                    "- `{}` ({}): {}{}",
                    e.path,
                    e.class,
                    e.why,
                    if e.candidates.is_empty() {
                        String::new()
                    } else {
                        format!("; candidates {}", e.candidates.join(", "))
                    }
                );
            }
            s += "\n";
        }
        let before: Vec<&SpecEntry> = r
            .spec
            .iter()
            .filter(|e| e.class == "refused_before")
            .collect();
        if !before.is_empty() {
            s += "**Spec entries the old revision already refused**\n\n";
            for e in before {
                let _ = writeln!(s, "- `{}`: {}", e.path, e.why);
            }
            s += "\n";
        }
        let renamed = r.spec.iter().filter(|e| e.class == "renamed").count();
        if renamed > 0 {
            let _ = writeln!(
                s,
                "{renamed} Spec entr{} carried to renamed features.\n",
                if renamed == 1 { "y was" } else { "ies were" }
            );
        }
        if !r.problems.added.is_empty() || !r.problems.resolved.is_empty() {
            s += "**Drawing problems**\n\n";
            for p in &r.problems.added {
                let _ = writeln!(s, "- new {} `{}`: {}", p.severity, p.code, p.message);
            }
            for p in &r.problems.resolved {
                let _ = writeln!(s, "- resolved `{}`", p.code);
            }
            s += "\n";
        }
        s
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_are_found_whole() {
        let s = "d:f:hole:3/location.length.x and face:12, not xf:hole:1 or f:hole:2b";
        let mut found = Vec::new();
        let mut rest = s;
        while let Some((a, b)) = next_id(rest) {
            found.push(rest[a..b].to_string());
            rest = &rest[b..];
        }
        assert_eq!(found, ["f:hole:3", "face:12"]);
    }

    #[test]
    fn renames_swap_at_once() {
        let m = Matching {
            features: [
                ("f:hole:3", Bind::To("f:hole:2".into())),
                ("f:hole:2", Bind::Orphaned),
                ("f:hole:4", Bind::To("f:hole:3".into())),
            ]
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .collect(),
            faces: BTreeMap::new(),
            records: BTreeMap::new(),
            same_faces: false,
        };
        let spec = serde_json::json!({"schema": "draftwright.spec/2", "binding": {"x": 1}, "sheet": {},
            "dimensions": {"d:f:hole:3/bore.diameter": {"tolerance": "+0.1 -0"}, "d:f:hole:4/location.length.x": {"tolerance": "±0.05"}, "d:f:hole:2/bore.diameter": {"tolerance": "±0.2"}},
            "notes": [{"id": "n:1", "text": "BREAK ALL SHARP EDGES"}]});
        let (carried, report) = m.carry(&spec);
        assert_eq!(
            carried["dimensions"],
            serde_json::json!({"d:f:hole:2/bore.diameter": {"tolerance": "+0.1 -0"}, "d:f:hole:3/location.length.x": {"tolerance": "±0.05"}})
        );
        assert!(carried.get("binding").is_none());
        assert_eq!(carried["notes"], spec["notes"]);
        let classes: Vec<(&str, &str)> = report
            .iter()
            .map(|e| (e.path.as_str(), e.class.as_str()))
            .collect();
        assert!(classes.contains(&("dimensions/d:f:hole:2/bore.diameter", "orphaned")));
        assert!(classes.contains(&("dimensions/d:f:hole:3/bore.diameter", "renamed")));
    }

    fn matching(same_faces: bool) -> Matching {
        Matching {
            features: [
                ("f:hole:3", Bind::To("f:hole:2".into())),
                ("f:hole:2", Bind::Orphaned),
            ]
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .collect(),
            faces: [
                (12, Bind::To("face:11".into())),
                (13, Bind::Orphaned),
                (14, Bind::To("face:13".into())),
            ]
            .into_iter()
            .collect(),
            records: BTreeMap::new(),
            same_faces,
        }
    }

    #[test]
    fn face_anchored_features_are_single_tokens() {
        let s = "d:f:hole:f21/bore.diameter|f:section_recess:f146_2 face:9";
        let mut rest = s;
        let mut found = Vec::new();
        while let Some((a, b)) = next_id(rest) {
            found.push(&rest[a..b]);
            rest = &rest[b..];
        }
        assert_eq!(found, ["f:hole:f21", "f:section_recess:f146_2", "face:9"]);
        let mut m = matching(true);
        m.features
            .insert("f:hole:f21".into(), Bind::To("f:hole:f77".into()));
        assert_eq!(
            m.rename("d:f:hole:f21/bore.diameter"),
            Ok("d:f:hole:f77/bore.diameter".into())
        );
    }

    #[test]
    fn embedded_faces_and_edges_are_found() {
        let s = "d:envelope/profile_angle.angle.f12 outer_profile:f12:e3:e4 x.f3.4 \
                 location.length.x f12 profile_angle.angle.f97_e92_e96 location.f9_2 hole_e1x";
        let mut found = Vec::new();
        let mut rest = s;
        while let Some((a, b)) = next_id(rest) {
            found.push(rest[a..b].to_string());
            rest = &rest[b..];
        }
        assert_eq!(
            found,
            ["f12", "f12", "e3", "e4", "f3.4", "f97", "e92", "e96", "f9"]
        );
    }

    #[test]
    fn embedded_faces_rename_and_edges_carry_only_unchanged() {
        let m = matching(false);
        assert_eq!(
            m.rename("d:envelope/profile_location_x.length.f14_2"),
            Ok("d:envelope/profile_location_x.length.f13_2".into())
        );
        assert_eq!(
            m.rename("d:envelope/profile_angle.angle.f13"),
            Err(("f13".into(), Bind::Orphaned))
        );
        assert_eq!(
            m.rename("outer_profile:f12:e3:e4"),
            Err(("e3".into(), Bind::Orphaned))
        );
        assert_eq!(
            matching(true).rename("notch:f12:e3:e4"),
            Ok("notch:f11:e3:e4".into())
        );
    }

    #[test]
    fn colliding_and_retargeted_entries_are_reported() {
        let spec = serde_json::json!({
            "dimensions": {
                "d:f:hole:3/bore.diameter": {"tolerance": "+0.1 -0"},
                "d:f:hole:2/bore.diameter": {"tolerance": "±0.2"},
            },
            "faces": {
                "a": {"faces": ["face:12"]},
                "b": {"faces": ["face:14"]},
                "c": {"faces": ["face:9"]},
            },
        });
        // An orphaned key set aside, not colliding; then two keys that both reach f:hole:2.
        let mut m = matching(false);
        m.features
            .insert("f:hole:2".into(), Bind::To("f:hole:2".into()));
        let (carried, report) = m.carry(&spec);
        assert_eq!(carried["dimensions"], serde_json::json!({}));
        let classes: Vec<(&str, &str)> = report
            .iter()
            .map(|e| (e.path.as_str(), e.class.as_str()))
            .collect();
        assert!(classes.contains(&("dimensions/d:f:hole:3/bore.diameter", "ambiguous")));
        assert!(classes.contains(&("dimensions/d:f:hole:2/bore.diameter", "ambiguous")));
        // Same keys, renamed faces: reported, not carried silently.
        assert!(classes.contains(&("faces/a", "renamed")));
        assert!(classes.contains(&("faces/b", "renamed")));
        assert!(!classes.iter().any(|(p, _)| *p == "faces/c"));
        assert_eq!(
            carried["faces"]["a"]["faces"],
            serde_json::json!(["face:11"])
        );
    }

    #[test]
    fn one_unmatched_sheet_setting_keeps_the_rest() {
        let spec = serde_json::json!({"sheet": {
            "size": "A3", "zones": false,
            "views": {"front": {"section": "face:13"}, "top": {"section": "face:12"}},
        }});
        let (carried, report) = matching(false).carry(&spec);
        assert_eq!(
            carried["sheet"],
            serde_json::json!({"size": "A3", "zones": false,
                "views": {"front": {}, "top": {"section": "face:11"}}})
        );
        let classes: Vec<(&str, &str)> = report
            .iter()
            .map(|e| (e.path.as_str(), e.class.as_str()))
            .collect();
        assert!(classes.contains(&("sheet/views/front/section", "orphaned")));
    }
}
