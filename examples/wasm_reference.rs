//! Native reference for tools/wasm-parity.test.mjs. Deliberately uses the core Session,
//! not the WASM adapter, so parity catches differences in loading and wrapper behaviour.
use std::io::{self, Read};
use std::path::Path;

use draftwright::{api, items::Op, model, session::Session, text::Font};
use serde::Deserialize;
use serde_json::json;

#[derive(Deserialize)]
struct Replay {
    name: String,
    part: String,
    steps: Vec<Step>,
}
#[derive(Deserialize)]
struct Step {
    edit: String,
    action: String,
    #[serde(default)]
    ops: Vec<Op>,
    #[serde(default)]
    spec: serde_json::Value,
}

fn emit(edit: &str, spec: &model::Spec, plan: &model::Plan, svg: String) {
    println!(
        "{}",
        json!({"edit":edit, "spec":serde_json::to_string(spec).unwrap(),
            "plan":serde_json::to_string(plan).unwrap(), "svg":svg})
    );
}

fn main() -> Result<(), api::Error> {
    let mut input = String::new();
    io::stdin()
        .read_to_string(&mut input)
        .map_err(|e| api::Error::invalid(e.to_string()))?;
    let replay: Replay =
        serde_json::from_str(&input).map_err(|e| api::Error::invalid(e.to_string()))?;
    let bytes = std::fs::read(&replay.part).map_err(|e| api::Error::invalid(e.to_string()))?;
    let (part, analysis) = model::analyse_native_step(Path::new(&replay.name), &bytes)?;
    let mut session = Session::new(part, analysis, model::Spec::default())?;
    emit(
        "open",
        session.spec(),
        session.plan(),
        session.drawing().svg(),
    );
    for step in replay.steps {
        match step.action.as_str() {
            "open_spec" => {
                println!(
                    "{}",
                    serde_json::to_string(&session.open_spec(&step.spec.to_string())?).unwrap()
                );
                continue;
            }
            "callout_offset" => {
                println!(
                    "{}",
                    serde_json::to_string(&session.callout_offset(&step.edit)?).unwrap()
                );
                continue;
            }
            "undo" => {
                session.undo()?;
            }
            "redo" => {
                session.redo()?;
            }
            "preview" => {
                let delta = session.apply(&step.ops, false)?;
                let mut state = session.state().clone();
                delta.apply_to(&mut state)?;
                let drawing = state.render(
                    session.part(),
                    session.analysis(),
                    &Font::new(api::MONO.to_vec()).unwrap(),
                    &mut model::Projections::default(),
                )?;
                emit(&step.edit, &state.plan.spec, &state.plan, drawing.svg());
                continue;
            }
            "apply" | "fragments" | "spec" => {
                session.apply(&step.ops, true)?;
            }
            other => panic!("unknown replay action {other}"),
        }
        emit(
            &step.edit,
            session.spec(),
            session.plan(),
            session.drawing().svg(),
        );
    }
    Ok(())
}
