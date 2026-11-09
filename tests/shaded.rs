//! Issue #372: vector pictorial styles, shared layout, and browser replay.
use draftwright::{
    Session, api,
    items::Op,
    model::{self, Spec},
    output::Item,
    text::Font,
};
use serde_json::json;
use std::path::Path;

fn plate() -> (draftwright::haecceity::Part, model::Analysis) {
    model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/plate.step"),
    )
    .unwrap()
}
#[test]
fn shaded_iso_is_vector_coloured_and_has_only_visible_hlr_edges() {
    let (part, a) = plate();
    let spec: Spec=serde_json::from_value(json!({"sheet":{"hidden":true,"centre_marks":true},"views":{"iso":{"style":"shaded","colour":[200,100,50],"hidden":true}}})).unwrap();
    let p = model::plan(&a, &spec).unwrap();
    let mut cache = model::Projections::default();
    let g = model::render_geometry(&part, &a, &p, &mut cache).unwrap();
    let iso = &g.views.iter().find(|(id, _)| id == "v:iso").unwrap().1;
    assert!(!iso.shaded.is_empty());
    assert!(iso.hidden.is_empty());
    assert!(!iso.visible.is_empty());
    assert!(
        iso.shaded
            .iter()
            .all(|p| p.points.len() >= 3 && p.colour[0] > p.colour[1] && p.colour[1] > p.colour[2])
    );
    let font = Font::new(api::MONO.to_vec()).unwrap();
    let d = model::render(&part, &a, &p, &font).unwrap();
    let fills: Vec<_> = d
        .layers
        .iter()
        .filter(|l| l.name.starts_with("shaded-iso-"))
        .collect();
    assert!(!fills.is_empty());
    assert!(fills.iter().all(|l| l.style.fill
        && l.style.width == 0.
        && l.items.iter().all(|i| matches!(i, Item::Face(_)))));
    assert!(
        d.annotations
            .iter()
            .all(|a| a.view.as_deref() != Some("v:iso"))
    );
    let svg = d.svg();
    assert!(svg.contains("id=\"shaded-iso-"));
    assert!(!svg.contains("<image"));
    assert!(svg.find("id=\"shaded-iso-").unwrap() < svg.find("id=\"part\"").unwrap());
    let pdf = d.pdf(&|name| (name == "IBMPlexMono-Regular.ttf").then(|| api::MONO.to_vec()));
    assert!(
        !pdf.windows(b"/Subtype /Image".len())
            .any(|w| w == b"/Subtype /Image")
    );
}
#[test]
fn underside_is_optional_and_shares_the_pictorial_zone() {
    let (part, a) = plate();
    let base = model::plan(&a, &Spec::default()).unwrap();
    assert!(base.views.iter().all(|v| v.id != "v:iso_below"));
    let spec=serde_json::from_value(json!({"views":{"iso":{"style":"shaded"},"iso_below":{"style":"shaded","direction":[-1,1,-1]}}})).unwrap();
    let p = model::plan_recovered(&a, &spec).unwrap();
    let top = p.views.iter().find(|v| v.id == "v:iso").unwrap();
    let bottom = p.views.iter().find(|v| v.id == "v:iso_below").unwrap();
    assert!(top.area[2] < bottom.area[0]);
    assert!(bottom.direction[2] < 0.);
    assert_eq!(top.sheet, bottom.sheet);
    let g = model::render_geometry(&part, &a, &p, &mut model::Projections::default()).unwrap();
    for (id, g) in g.views.iter().filter(|(id, _)| id.starts_with("v:iso")) {
        assert!(!g.shaded.is_empty(), "{id}");
        assert!(g.hidden.is_empty());
        let v = p.views.iter().find(|v| &v.id == id).unwrap();
        assert!(
            g.shaded
                .iter()
                .flat_map(|p| &p.points)
                .all(|q| q[0] >= v.area[0]
                    && q[0] <= v.area[2]
                    && q[1] >= v.area[1]
                    && q[1] <= v.area[3])
        );
    }
}
#[test]
fn invalid_camera_and_orthographic_shading_are_refused() {
    let (_, a) = plate();
    for value in [
        json!({"views":{"front":{"style":"shaded"}}}),
        json!({"views":{"iso":{"direction":[0,0,1]}}}),
        json!({"views":{"iso":{"direction":[0,0,0]}}}),
    ] {
        let s = serde_json::from_value(value).unwrap();
        assert!(model::plan(&a, &s).is_err());
    }
    assert!(serde_json::from_value::<Spec>(json!({"views":{"iso":{"colour":[256,0,0]}}})).is_err());
}
#[test]
fn session_style_colour_and_camera_changes_replay_and_undo() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/plate.step");
    let mut s = Session::open(&path, api::Features::None, Spec::default()).unwrap();
    assert_eq!(
        s.options("v:iso").unwrap().settable["views/iso/style"]["choices"],
        json!(["wireframe", "shaded"])
    );
    let mut replay = s.state().clone();
    let delta = s
        .apply(&[Op::set("views/iso/style", json!("shaded"))], true)
        .unwrap();
    delta.apply_to(&mut replay).unwrap();
    assert_eq!(
        serde_json::to_value(&replay).unwrap(),
        serde_json::to_value(s.state()).unwrap()
    );
    assert!(s.svg_fragments(Some(&delta))["v:iso"].contains("shaded-iso-"));
    let shaded = s.drawing().svg();
    assert!(shaded.contains("shaded-iso-"));
    s.apply(&[Op::set("views/iso/colour", json!([220, 100, 60]))], true)
        .unwrap();
    assert_ne!(shaded, s.drawing().svg());
    s.undo().unwrap();
    assert_eq!(shaded, s.drawing().svg());
    let delta = s
        .apply(&[Op::set("views/iso/direction", json!([-1, 1, -1]))], true)
        .unwrap();
    assert!(delta.geometry.views.iter().any(|(id, _)| id == "v:iso"));
    assert_eq!(
        s.plan()
            .views
            .iter()
            .find(|v| v.id == "v:iso")
            .unwrap()
            .direction,
        [-1., 1., -1.]
    );
    s.undo().unwrap();
    assert_eq!(shaded, s.drawing().svg());
}
