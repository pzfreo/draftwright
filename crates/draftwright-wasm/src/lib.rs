//! A wasm-bindgen front end over draftwright's live Session and operation schema.
pub use draftwright::api::Error;
pub use draftwright::session::Session as CoreSession;
use draftwright::{api, items::Op, model, session};

pub fn error_json(error: &Error) -> String {
    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
    serde_json::to_string(error).expect("errors serialise")
}

pub struct Session(pub CoreSession);
/// [`api::spec_ops`] over two Specs' JSON, answered as the ops' JSON.
pub fn spec_ops_json(before: &str, after: &str) -> Result<String, Error> {
    let read = |json: &str| {
        serde_json::from_str::<serde_json::Value>(json)
            .map_err(|e| Error::invalid(format!("invalid spec: {e}")))
    };
    // Invariant: Ops serialize as JSON objects; serialization performs no external I/O.
    Ok(
        serde_json::to_string(&api::spec_ops(&read(before)?, &read(after)?))
            .expect("ops serialise"),
    )
}

impl Session {
    pub fn open(name: &str, bytes: &[u8]) -> Result<Self, Error> {
        Self::open_part(name, bytes, None)
    }
    /// [`Session::open`] of part *part* of an assembly alone, when given.
    pub fn open_part(name: &str, bytes: &[u8], part: Option<usize>) -> Result<Self, Error> {
        let (part, analysis) = model::analyse_native_bytes_part(name, bytes, part)?;
        CoreSession::new(part, analysis, model::Spec::default()).map(Self)
    }
    pub fn apply(&mut self, ops: &[Op], commit: bool) -> Result<session::Delta, Error> {
        self.0.apply(ops, commit)
    }
    pub fn apply_json(&mut self, json: &str, commit: bool) -> Result<String, Error> {
        let ops: Vec<Op> =
            serde_json::from_str(json).map_err(|e| Error::invalid(format!("invalid ops: {e}")))?;
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        Ok(serde_json::to_string(&self.apply(&ops, commit)?).expect("delta serialises"))
    }
    pub fn apply_fragments_json(&mut self, json: &str) -> Result<String, Error> {
        let ops: Vec<Op> = serde_json::from_str(json).map_err(|e| Error::invalid(e.to_string()))?;
        let delta = self.apply(&ops, true)?;
        let annotations: std::collections::BTreeMap<_, _> =
            delta.annotations.keys().map(|id| (id, true)).collect();
        let changed: Vec<_> = delta.changed.iter().map(|i| serde_json::json!({"item":{"id":i.item.id},"placement":i.placement.as_ref().map(|_|true)})).collect();
        let mut packet = serde_json::json!({"delta":{"plans":delta.plans,"plan":delta.plan,"removed":delta.removed,"changed":changed,"annotations":annotations,"problems_added":delta.problems_added,"problems_resolved":delta.problems_resolved,"notices":delta.notices,"problem_order":delta.problem_order},"fragments":self.0.svg_fragments(Some(&delta)),"pmi":self.0.pmi_report()});
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        let plan = serde_json::to_value(self.0.plan()).unwrap();
        for (name, patch) in packet["delta"]["plan"]["collections"]
            .as_object_mut()
            // Invariant: packet is constructed above with an object-valued delta.plan.collections.
            .unwrap()
        {
            patch["present"] = serde_json::Value::Bool(plan.get(name.as_str()).is_some());
        }
        if ops.iter().any(|op| {
            let path = op.path.trim_start_matches('/');
            path.starts_with("views") || path.starts_with("dimension_schemes")
        }) {
            packet["placementOptions"] =
                // Invariant: placement_options_json produces this JSON locally; no caller text is reparsed.
                serde_json::from_str(&self.placement_options_json()?).unwrap();
        }
        Ok(packet.to_string())
    }
    pub fn plans(&self) -> usize {
        self.0.stats().plans
    }
    pub fn source_sha256(&self) -> &str {
        &self.0.analysis().binding.source_sha256
    }
    pub fn face_count(&self) -> usize {
        self.0.part().faces.len()
    }
    pub fn feature_count(&self) -> usize {
        self.0.analysis().features.len()
    }
    pub fn analysis_json(&self) -> String {
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        serde_json::to_string(self.0.analysis()).unwrap()
    }
    pub fn provenance_json(&self) -> String {
        serde_json::json!({"source_sha256": self.source_sha256(), "face_entities": (0..self.face_count()).map(|i| self.0.part().face_source(i).map(|s| s.entity)).collect::<Vec<_>>()}).to_string()
    }
    pub fn spec_json(&self) -> String {
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        serde_json::to_string(self.0.spec()).unwrap()
    }
    pub fn plan_json(&self) -> Result<String, Error> {
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        Ok(serde_json::to_string(self.0.plan()).unwrap())
    }
    pub fn render_svg(&self) -> Result<String, Error> {
        Ok(self.0.drawing().svg())
    }
    /// One sheet's SVG fragments, as `fragments()` gives sheet 1's.
    pub fn sheet_fragments_json(&self, sheet: u32) -> Result<String, Error> {
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        Ok(serde_json::to_string(&self.0.svg_fragments_on_sheet(None, sheet)?).unwrap())
    }
    /// One sheet's SVG.
    pub fn render_sheet_svg(&self, sheet: u32) -> Result<String, Error> {
        Ok(self.0.drawing_on_sheet(sheet)?.svg())
    }
    /// The drawing as a PDF, a page per sheet, from the engine's own writer with its bundled
    /// fonts embedded.
    pub fn render_pdf(&self) -> Result<Vec<u8>, Error> {
        let sheets = std::iter::once(1).chain(self.0.plan().sheets.iter().map(|s| s.number));
        let drawings = sheets
            .map(|sheet| self.0.drawing_on_sheet(sheet))
            .collect::<Result<Vec<_>, _>>()?;
        Ok(draftwright::output::Drawing::pdf_pages(
            &drawings,
            &|name| match name {
                "IBMPlexMono-Regular.ttf" => Some(api::MONO.to_vec()),
                draftwright::title_block::FONT => {
                    Some(draftwright::title_block::FONT_DATA.to_vec())
                }
                _ => None,
            },
        ))
    }
    pub fn set_pmi(&mut self, source: &str, bytes: &[u8]) -> Result<(), Error> {
        if source != self.source_sha256() {
            return Err(Error::invalid("PMI was generated from another source file"));
        }
        self.0.set_pmi(bytes).map(|_| ())
    }
    pub fn set_pmi_ops(&mut self, source: &str, bytes: &[u8], json: &str) -> Result<(), Error> {
        if source != self.source_sha256() {
            return Err(Error::invalid("PMI was generated from another source file"));
        }
        let ops: Vec<Op> = serde_json::from_str(json).map_err(|e| Error::invalid(e.to_string()))?;
        self.0.set_pmi_ops(bytes, &ops).map(|_| ())
    }
    pub fn set_spec(&mut self, json: &str) -> Result<(), Error> {
        let spec: model::Spec =
            serde_json::from_str(json).map_err(|e| Error::invalid(format!("invalid spec: {e}")))?;
        self.0.set_spec(&spec)
    }
    /// [`CoreSession::open_spec`], answered as JSON: `{spec, set_aside: [{path, entry, reason_code}], migrated: [{path, from, to}], ids: {old: new}}`.
    pub fn open_spec_json(&mut self, json: &str) -> Result<String, Error> {
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        Ok(serde_json::to_string(&self.0.open_spec(json)?).expect("opened spec serialises"))
    }
    pub fn callout_offset_json(&self, address: &str) -> Result<String, Error> {
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        Ok(serde_json::to_string(&self.0.callout_offset(address)?).unwrap())
    }
    pub fn label_offset_json(&self, address: &str) -> Result<String, Error> {
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        Ok(serde_json::to_string(&self.0.label_offset(address)?).unwrap())
    }
    pub fn placement_options_json(&self) -> Result<String, Error> {
        let mut out = serde_json::Map::new();
        let mut choices = self.0.all_options();
        for answer in self.0.items(&session::ItemFilter::default()) {
            let options = choices.remove(&answer.item.id).ok_or_else(|| {
                Error::invalid(format!(
                    "No placement options for item {:?}.",
                    answer.item.id
                ))
            })?;
            if !options.operations.contains(&"omit".into()) {
                continue;
            }
            let mut views = std::collections::BTreeMap::<String, Vec<String>>::new();
            for place in options.placements {
                if let (Some(view), Some(side)) = (place.view, place.side) {
                    views.entry(view).or_default().push(side.name().into());
                }
            }
            let mut entry = serde_json::json!({"label": answer.item.label, "views": views.into_iter().map(|(view,sides)| serde_json::json!({"view":view,"sides":sides})).collect::<Vec<_>>(), "operations": options.operations, "operation_scopes":options.operation_scopes});
            if let Some(kind @ ("callout" | "linear")) = answer.dimension_kind.as_deref() {
                entry["offset"] = kind.into();
            }
            out.insert(answer.item.id, entry);
        }
        Ok(serde_json::Value::Object(out).to_string())
    }
    pub fn numbered_characteristics_json(&self) -> String {
        serde_json::to_string(self.0.numbered_characteristics()).expect("characteristics serialize")
    }
    pub fn pmi_json(&self) -> String {
        self.0.pmi_report().to_string()
    }
    pub fn snapshot(&self) -> Result<String, Error> {
        // Invariant: The reparsed documents here were serialized locally above, not supplied by the caller.
        Ok(serde_json::json!({"problems":self.0.state().problems,"plan":self.0.plan(),"spec":self.0.spec(),"svg":self.0.drawing().svg(),"placementOptions":serde_json::from_str::<serde_json::Value>(&self.placement_options_json()?).unwrap(),"provenance":serde_json::from_str::<serde_json::Value>(&self.provenance_json()).unwrap(),"analysis":self.0.analysis(),"pmi":serde_json::from_str::<serde_json::Value>(&self.pmi_json()).unwrap()}).to_string())
    }
    pub fn preview(&mut self, json: &str) -> Result<String, Error> {
        let ops: Vec<Op> =
            serde_json::from_str(json).map_err(|e| Error::invalid(format!("invalid ops: {e}")))?;
        let delta = self.apply(&ops, false)?;
        let mut state = self.0.state().clone();
        delta.apply_to(&mut state)?;
        let drawing = state.render(
            self.0.part(),
            self.0.analysis(),
            // Invariant: Font bytes are bundled at build time and parsed unchanged, independently of user input.
            &draftwright::text::Font::new(api::MONO.to_vec()).unwrap(),
            &mut model::Projections::default(),
        )?;
        // Invariant: snapshot serializes a JSON object locally.
        let mut snapshot: serde_json::Value = serde_json::from_str(&self.snapshot()?).unwrap();
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        snapshot["problems"] = serde_json::to_value(&state.problems).unwrap();
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        snapshot["plan"] = serde_json::to_value(&state.plan).unwrap();
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        snapshot["spec"] = serde_json::to_value(&state.plan.spec).unwrap();
        snapshot["notices"] = serde_json::to_value(&delta.notices).unwrap();
        snapshot["svg"] = serde_json::Value::String(drawing.svg());
        Ok(snapshot.to_string())
    }
}

/// In-memory pack state shared by native tests and the wasm-bindgen adapter.
/// A rebuild is transactional: an invalid spec leaves the last pack intact.
pub struct PackSession {
    name: String,
    bytes: Vec<u8>,
    spec: draftwright::pack::PackSpec,
    pack: draftwright::pack::Pack,
}

impl PackSession {
    pub fn open(name: &str, bytes: &[u8], spec_json: Option<&str>) -> Result<Self, Error> {
        let spec = Self::parse_spec(spec_json.unwrap_or("{}"))?;
        let pack = draftwright::pack::build_bytes(name, bytes, &spec)?;
        Ok(Self {
            name: name.into(),
            bytes: bytes.into(),
            spec,
            pack,
        })
    }
    fn parse_spec(json: &str) -> Result<draftwright::pack::PackSpec, Error> {
        serde_json::from_str(json).map_err(|e| Error::invalid(format!("invalid pack spec: {e}")))
    }
    pub fn report_json(&self) -> String {
        serde_json::to_string_pretty(&self.pack.report).expect("report serialises")
    }
    pub fn spec_json(&self) -> String {
        serde_json::to_string(&self.spec).expect("spec serialises")
    }
    pub fn page_count(&self) -> usize {
        self.pack.drawings.len()
    }
    pub fn page_svg(&self, page: usize) -> Result<String, Error> {
        self.pack.page_svg(page)
    }
    pub fn pdf(&self) -> Vec<u8> {
        self.pack.pdf()
    }
    pub fn csv(&self) -> String {
        self.pack.report.csv()
    }
    pub fn set_spec(&mut self, json: &str) -> Result<(), Error> {
        let spec = Self::parse_spec(json)?;
        let pack = draftwright::pack::build_bytes(&self.name, &self.bytes, &spec)?;
        self.spec = spec;
        self.pack = pack;
        Ok(())
    }
}

/// The CLI `parts` document, from STEP or gzip bytes.
pub fn list_parts_json(name: &str, bytes: &[u8]) -> Result<String, Error> {
    let parts = model::parts(std::path::Path::new(name), bytes)?;
    Ok(
        serde_json::to_string_pretty(&serde_json::json!({"parts": parts}))
            .expect("parts serialise"),
    )
}

#[cfg(target_arch = "wasm32")]
pub mod wasm {
    use super::{Session, error_json, spec_ops_json};
    use wasm_bindgen::prelude::*;
    fn js(e: super::Error) -> JsValue {
        JsValue::from_str(&error_json(&e))
    }
    // calloutOffset still returns a JSON string. These describe its parsed payload.
    #[wasm_bindgen(typescript_custom_section)]
    const CALLOUT_OFFSET_TYPES: &str = r#"
export interface CalloutSpan {
    start: number;
    sweep: number;
}
export interface CalloutMember {
    centre: [number, number];
    radius: number;
    span?: CalloutSpan;
}
export interface CalloutOffset {
    view: string;
    tip: [number, number];
    bend: [number, number];
    offset: [number, number];
    members: CalloutMember[];
}
"#;
    #[wasm_bindgen(start)]
    pub fn start() {
        console_error_panic_hook::set_once();
    }
    /// The `set` and `remove` ops that turn Spec *before* into *after* (JSON), as JSON.
    #[wasm_bindgen(js_name = specOps)]
    pub fn spec_ops(before: &str, after: &str) -> Result<String, JsValue> {
        spec_ops_json(before, after).map_err(js)
    }
    #[wasm_bindgen(js_name = listParts)]
    pub fn list_parts(name: &str, bytes: &[u8]) -> Result<String, JsValue> {
        super::list_parts_json(name, bytes).map_err(js)
    }

    #[wasm_bindgen]
    pub struct Pack(super::PackSession);
    #[wasm_bindgen]
    impl Pack {
        #[wasm_bindgen(constructor)]
        #[allow(non_snake_case)]
        pub fn new(
            name: &str,
            bytes: &[u8],
            #[wasm_bindgen(unchecked_optional_param_type = "string")] specJson: Option<String>,
        ) -> Result<Pack, JsValue> {
            super::PackSession::open(name, bytes, specJson.as_deref())
                .map(Pack)
                .map_err(js)
        }
        #[wasm_bindgen(js_name = reportJson)]
        pub fn report_json(&self) -> String {
            self.0.report_json()
        }
        #[wasm_bindgen(js_name = specJson)]
        pub fn spec_json(&self) -> String {
            self.0.spec_json()
        }
        #[wasm_bindgen(js_name = pageCount)]
        pub fn page_count(&self) -> usize {
            self.0.page_count()
        }
        #[wasm_bindgen(js_name = pageSvg)]
        pub fn page_svg(&self, page: usize) -> Result<String, JsValue> {
            self.0.page_svg(page).map_err(js)
        }
        pub fn pdf(&self) -> Vec<u8> {
            self.0.pdf()
        }
        pub fn csv(&self) -> String {
            self.0.csv()
        }
        #[wasm_bindgen(js_name = setSpec)]
        #[allow(non_snake_case)]
        pub fn set_spec(&mut self, specJson: &str) -> Result<(), JsValue> {
            self.0.set_spec(specJson).map_err(js)
        }
    }

    #[wasm_bindgen]
    pub struct Engine(Session);
    #[wasm_bindgen]
    impl Engine {
        #[wasm_bindgen(constructor)]
        pub fn open(name: &str, bytes: &[u8]) -> Result<Engine, JsValue> {
            Session::open(name, bytes).map(Engine).map_err(js)
        }
        /// Part *part* of an assembly (an index as `parts` lists them) alone, in its own frame
        /// and with its PMI only.
        #[wasm_bindgen(js_name = openPart)]
        pub fn open_part(name: &str, bytes: &[u8], part: usize) -> Result<Engine, JsValue> {
            Session::open_part(name, bytes, Some(part))
                .map(Engine)
                .map_err(js)
        }
        pub fn apply(&mut self, ops: &str, commit: bool) -> Result<String, JsValue> {
            self.0.apply_json(ops, commit).map_err(js)
        }
        pub fn fragments(&self) -> String {
            // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
            serde_json::to_string(&self.0.0.svg_fragments(None)).unwrap()
        }
        #[wasm_bindgen(js_name = applyFragments)]
        pub fn apply_fragments(&mut self, ops: &str) -> Result<String, JsValue> {
            self.0.apply_fragments_json(ops).map_err(js)
        }
        pub fn preview(&mut self, ops: &str) -> Result<String, JsValue> {
            self.0.preview(ops).map_err(js)
        }
        pub fn snapshot(&self) -> Result<String, JsValue> {
            self.0.snapshot().map_err(js)
        }
        pub fn undo(&mut self) -> Result<String, JsValue> {
            self.0
                .0
                .undo()
                // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
                .map(|d| serde_json::to_string(&d).unwrap())
                .map_err(js)
        }
        pub fn redo(&mut self) -> Result<String, JsValue> {
            self.0
                .0
                .redo()
                // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
                .map(|d| serde_json::to_string(&d).unwrap())
                .map_err(js)
        }
        #[wasm_bindgen(js_name = numberedCharacteristics)]
        pub fn numbered_characteristics(&self) -> String {
            self.0.numbered_characteristics_json()
        }
        #[wasm_bindgen(js_name = setPmi)]
        pub fn set_pmi(&mut self, source: &str, bytes: &[u8]) -> Result<(), JsValue> {
            self.0.set_pmi(source, bytes).map_err(js)
        }
        #[wasm_bindgen(js_name = setPmiOps)]
        pub fn set_pmi_ops(
            &mut self,
            source: &str,
            bytes: &[u8],
            ops: &str,
        ) -> Result<(), JsValue> {
            self.0.set_pmi_ops(source, bytes, ops).map_err(js)
        }
        #[wasm_bindgen(js_name = setSpec)]
        pub fn set_spec(&mut self, json: &str) -> Result<(), JsValue> {
            self.0.set_spec(json).map_err(js)
        }
        /// Open a saved Spec, setting aside entries the part no longer has:
        /// `{spec, set_aside: [{path, entry, reason_code}], migrated: [{path, from, to}], ids: {old: new}}`.
        #[wasm_bindgen(js_name = openSpec)]
        pub fn open_spec(&mut self, json: &str) -> Result<String, JsValue> {
            self.0.open_spec_json(json).map_err(js)
        }
        #[wasm_bindgen(js_name = planCount)]
        pub fn plans(&self) -> usize {
            self.0.plans()
        }
        #[wasm_bindgen(js_name = faceCount)]
        pub fn face_count(&self) -> usize {
            self.0.face_count()
        }
        #[wasm_bindgen(js_name = featureCount)]
        pub fn feature_count(&self) -> usize {
            self.0.feature_count()
        }
        pub fn plan(&self) -> Result<String, JsValue> {
            self.0.plan_json().map_err(js)
        }
        pub fn spec(&self) -> String {
            self.0.spec_json()
        }
        pub fn pmi(&self) -> String {
            self.0.pmi_json()
        }
        /// JSON-encoded CalloutOffset; coordinates are paper mm (y up), spans CCW radians.
        #[wasm_bindgen(js_name = calloutOffset)]
        pub fn callout_offset(&self, address: &str) -> Result<String, JsValue> {
            self.0.callout_offset_json(address).map_err(js)
        }
        #[wasm_bindgen(js_name = labelOffset)]
        pub fn label_offset(&self, address: &str) -> Result<String, JsValue> {
            self.0.label_offset_json(address).map_err(js)
        }
        #[wasm_bindgen(js_name = placementOptions)]
        pub fn placement_options(&self) -> Result<String, JsValue> {
            self.0.placement_options_json().map_err(js)
        }
        #[wasm_bindgen(js_name = renderSvg)]
        pub fn render_svg(&self) -> Result<Vec<u8>, JsValue> {
            self.0.render_svg().map(String::into_bytes).map_err(js)
        }
        #[wasm_bindgen(js_name = renderPdf)]
        pub fn render_pdf(&self) -> Result<Vec<u8>, JsValue> {
            self.0.render_pdf().map_err(js)
        }
        #[wasm_bindgen(js_name = sheetFragments)]
        pub fn sheet_fragments(&self, sheet: u32) -> Result<String, JsValue> {
            self.0.sheet_fragments_json(sheet).map_err(js)
        }
        #[wasm_bindgen(js_name = sheetSvg)]
        pub fn sheet_svg(&self, sheet: u32) -> Result<Vec<u8>, JsValue> {
            self.0
                .render_sheet_svg(sheet)
                .map(String::into_bytes)
                .map_err(js)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};

    #[test]
    fn previews_and_fragment_edits_report_sheet_changes() {
        let mut s = Session::open(
            "prismatic.step",
            include_bytes!("../../../tests/fixtures/parts/prismatic.step"),
        )
        .unwrap();
        let before = s.0.spec().clone();
        let size = if s.0.plan().sheet.size == "A3" {
            "A2"
        } else {
            "A3"
        };
        let ops = json!([
            {"op":"set","path":"sheet/size","value":size},
            {"op":"set","path":"sheet/scale","value":"1:1"}
        ])
        .to_string();
        let preview: Value = serde_json::from_str(&s.preview(&ops).unwrap()).unwrap();
        assert_eq!(s.0.spec(), &before, "preview leaves the session unchanged");
        assert_eq!(preview["plan"]["sheet"]["size"], size);
        let notice = &preview["notices"][0];
        assert_eq!(notice["code"], "layout_replanned");
        assert!(
            notice["message"]
                .as_str()
                .unwrap()
                .contains("The whole sheet is laid out again")
        );
        let applied: Value = serde_json::from_str(&s.apply_fragments_json(&ops).unwrap()).unwrap();
        assert_eq!(applied["delta"]["notices"], preview["notices"]);
        assert_eq!(s.0.plan().sheet.size, size);
    }

    #[test]
    fn opens_one_part_of_an_assembly_alone() {
        // The plate (part 0) and the pin (part 1, placed twice) of tests/fixtures/assembly.
        let bytes = include_bytes!("../../../tests/fixtures/assembly/assembly.step");
        let plate = Session::open_part("assembly.step", bytes, Some(0)).unwrap();
        let pin = Session::open_part("assembly.step", bytes, Some(1)).unwrap();
        assert_eq!(plate.face_count(), 13);
        assert_eq!(pin.face_count(), 7);
        assert!(
            Session::open_part("assembly.step", bytes, Some(2)).is_err(),
            "no third part"
        );
    }

    #[test]
    fn json_exposes_scoped_fixes_and_placement_operations() {
        let s = Session::open(
            "editor-plate.step",
            include_bytes!("../../../tests/fixtures/samples/editor-plate.step"),
        )
        .unwrap();
        let options: Value = serde_json::from_str(&s.placement_options_json().unwrap()).unwrap();
        let id = "d:f:hole_pattern:1/bore.diameter";
        assert_eq!(options[id]["operation_scopes"]["place"], "local");
        assert_eq!(options["v:iso"]["operation_scopes"]["omit"], "view");
        let snapshot: Value = serde_json::from_str(&s.snapshot().unwrap()).unwrap();
        assert_eq!(snapshot["problems"], snapshot["plan"]["problems"]);
        let mut fixes = 0;
        for problem in snapshot["problems"].as_array().unwrap() {
            assert!(
                ["incomplete", "decision", "readability", "advisory"]
                    .contains(&problem["impact"].as_str().unwrap())
            );
            assert!(!problem["group"].as_str().unwrap().is_empty());
            if problem["fix"].is_array() {
                assert_eq!(problem["fix"], problem["fixes"][0]["ops"]);
                assert!(
                    ["local", "view", "sheet"]
                        .contains(&problem["fixes"][0]["scope"].as_str().unwrap())
                );
                fixes += 1;
            }
        }
        assert!(fixes > 0);
    }

    #[test]
    fn offset_queries_advertise_forms_and_pin_an_interior_callout_without_bouncing() {
        let mut s = Session::open(
            "editor-plate.step",
            include_bytes!("../../../tests/fixtures/samples/editor-plate.step"),
        )
        .unwrap();
        let id = "d:f:hole_pattern:1/bore.diameter";
        let before =
            s.0.plan()
                .dimensions
                .iter()
                .find(|d| d.id == id)
                .unwrap()
                .placed
                .clone()
                .unwrap();
        let at: Value = serde_json::from_str(&s.callout_offset_json(id).unwrap()).unwrap();
        assert_eq!(
            at,
            serde_json::to_value(s.0.callout_offset(id).unwrap()).unwrap()
        );
        assert_eq!(at.as_object().unwrap().len(), 5);
        assert_eq!(at["members"].as_array().unwrap().len(), 6);
        let view = s.0.plan().views.iter().find(|v| v.id == "v:plan").unwrap();
        let bend = before.lines[0][1];
        assert!(
            (bend[0] - view.at[0]).abs() < 50. * view.scale
                && (bend[1] - view.at[1]).abs() < 30. * view.scale
        );
        s.apply_json(&json!([{"op":"set","path":format!("dimensions/{}/place",draftwright::items::key(id)),"value":{"offset":at["offset"],"pinned":true}}]).to_string(),true).unwrap();
        assert_eq!(
            s.0.plan()
                .dimensions
                .iter()
                .find(|d| d.id == id)
                .unwrap()
                .placed
                .as_ref(),
            Some(&before)
        );
        s.0.undo().unwrap();
        let linear = "d:envelope/width.length";
        let label: Value = serde_json::from_str(&s.label_offset_json(linear).unwrap()).unwrap();
        assert_eq!(
            label,
            serde_json::to_value(s.0.label_offset(linear).unwrap()).unwrap()
        );
        assert_eq!(
            label
                .as_object()
                .unwrap()
                .keys()
                .cloned()
                .collect::<Vec<_>>(),
            ["anchor", "label", "offset", "view"]
        );
        let options: Value = serde_json::from_str(&s.placement_options_json().unwrap()).unwrap();
        assert_eq!(options[id]["offset"], "callout");
        assert_eq!(options[linear]["offset"], "linear");
        assert!(
            options
                .as_object()
                .unwrap()
                .values()
                .any(|entry| entry.get("offset").is_none())
        );
        assert!(s.callout_offset_json(linear).is_err());
        assert!(s.label_offset_json(id).is_err());
        assert!(s.callout_offset_json("unknown").is_err());
        assert!(s.label_offset_json("unknown").is_err());
    }

    #[test]
    fn open_spec_and_spec_ops_answer_json() {
        let mut s = Session::open(
            "mounting_plate.step",
            include_bytes!("../../../tests/fixtures/parts/mounting_plate.step"),
        )
        .unwrap();
        let saved = json!({"sheet":{"preferred_printer":"A3"},
            "dimensions":{"d:f:hole:1/bore.diameter":{"omit":true}}});
        let refused: Value =
            serde_json::from_str(&error_json(&s.set_spec(&saved.to_string()).unwrap_err()))
                .unwrap();
        assert_eq!(refused["code"], "invalid");
        let opened: Value =
            serde_json::from_str(&s.open_spec_json(&saved.to_string()).unwrap()).unwrap();
        assert_eq!(opened["set_aside"], json!([]));
        assert_eq!(opened["spec"]["dimensions"], saved["dimensions"]);
        assert!(
            s.0.plan()
                .problems
                .iter()
                .any(|p| p.code == "dimension_edit_unapplied")
        );
        assert!(s.render_svg().unwrap().contains("<svg"));
        assert_eq!(opened["spec"]["sheet"]["preferred_max_size"], "A3");
        let current: Value = serde_json::from_str(&s.spec_json()).unwrap();
        assert_eq!(opened["spec"], current);
        let ops: Value = serde_json::from_str(
            &spec_ops_json(r#"{"a":{"b/c":1}}"#, r#"{"a":{"b/c":2},"d":[1]}"#).unwrap(),
        )
        .unwrap();
        assert_eq!(
            ops,
            json!([{"op":"set","path":"a/b~1c","value":2},{"op":"set","path":"d","value":[1]}])
        );
        assert!(spec_ops_json("{", "{}").is_err());
    }
}

#[cfg(test)]
mod hostile_tests {
    use super::*;

    #[test]
    fn deep_edit_paths_refuse_before_building_recursive_values() {
        let mut session = Session::open(
            "prismatic.step",
            include_bytes!("../../../tests/fixtures/parts/prismatic.step"),
        )
        .unwrap();
        let before = session.0.spec().clone();
        let ops = serde_json::json!([{"op":"set", "path":format!("sheet/{}value", "nested/".repeat(20_000)), "value":1}]);
        let error = session.apply_json(&ops.to_string(), true).unwrap_err();
        assert_eq!(error.code, "invalid");
        assert!(error.message.contains("64 components"));
        assert_eq!(session.0.spec(), &before);
        session
            .apply_json(
                r#"[{"op":"set","path":"sheet/title/material","value":"Steel"}]"#,
                true,
            )
            .unwrap();
        assert_eq!(
            session
                .0
                .spec()
                .sheet
                .title
                .get("material")
                .map(String::as_str),
            Some("Steel")
        );
    }

    #[test]
    fn truncated_recognition_refuses_transactionally() {
        // Recognition JSON is a native bridge input; the reviewed browser API accepts
        // STEP/PMI bytes only. Test the actual bridge here as well as JSON bytes in JS.
        let session = Session::open(
            "prismatic.step",
            include_bytes!("../../../tests/fixtures/parts/prismatic.step"),
        )
        .unwrap();
        let mut analysis = session.0.analysis().clone();
        let before = serde_json::to_value(&analysis).unwrap();
        assert!(
            model::recognise(
                session.0.part(),
                &mut analysis,
                r#"{"format":"draftwright.recognition/1","quiddity":{"#
            )
            .is_err()
        );
        assert_eq!(serde_json::to_value(&analysis).unwrap(), before);
        assert!(session.render_svg().unwrap().contains("<svg"));
    }
}
