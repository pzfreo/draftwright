//! Unit fixtures for rendered lint evidence, outside the implementation independence boundary.

use crate::lint::hole_patterns::concentric_on_drawing;
use crate::model::Analysis;
use crate::output::{Curve, Drawing, Drawn, DrawnFrame, DrawnView, Kind, Sheet};
use serde_json::json;

#[test]
fn rounded_concentric_centres_locate_only_the_drawn_radial_axes() {
    for deep in 0..3 {
        let mut bounds = [[-10.; 3], [10.; 3]];
        bounds[0][deep] = -1.;
        bounds[1][deep] = 1.;
        let mut direction = [0.; 3];
        direction[deep] = 1.;
        let mut up = [0.; 3];
        up[(deep + 1) % 3] = 1.;
        let mut a: Analysis = serde_json::from_value(json!({
            "schema": crate::model::ANALYSIS_SCHEMA,
            "binding": {"source_sha256":"", "part":"round", "face_count":1, "loader":"test"},
            "envelope": bounds, "iso_bounds":[-10.,-10.,10.,10.],
            "solid_count":1, "features":[],
            "recognition": {
                "recogniser":"test", "frame":[[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]],
                "patterns":[{"id":"f:hole_pattern:1", "arrangement":"bolt_circle",
                    "members":[], "faces":[], "parameters":{"center":[0.0005,0.0005,0.0005], "diameter":12.}}]
            },
            "edge_ink":{"edges":[], "planes":[], "curved":[{
                "bounds":bounds, "cylinder":{"origin":[0.,0.,0.], "axis":direction, "radius":10.}
            }]}
        })).unwrap();
        let mut sheet = Sheet::default();
        sheet.frames.insert(
            "end".into(),
            DrawnFrame {
                direction,
                up,
                at: [0., 0.],
                scale: 1.,
                half_axis: None,
                broken: None,
            },
        );
        sheet.centre_marks.insert("end".into(), vec![[0., 0.]]);
        sheet.views.push(DrawnView {
            name: "v:end".into(),
            bbox: [-10., -10., 10., 10.],
            edges: vec![Curve::Conic {
                centre: [0., 0.],
                radii: [10., 10.],
                x_axis: [1., 0.],
                y_axis: [0., 1.],
                t0: 0.,
                t1: std::f64::consts::TAU,
            }],
        });
        let points: Vec<_> = (0..64)
            .map(|i| {
                let angle = std::f64::consts::TAU * i as f64 / 64.;
                [6. * angle.cos(), 6. * angle.sin()]
            })
            .collect();
        sheet.items.push(Drawn {
            name: "ink:v:end:f:hole_pattern:1".into(),
            kind: Kind::Centreline,
            ink: [-6., -6., 6., 6.],
            label: None,
            lines: (0..points.len())
                .map(|i| [points[i], points[(i + 1) % points.len()]])
                .collect(),
            heads: vec![],
            datum_triangle: None,
            elbow: None,
            interior: false,
        });
        let mut drawing = Drawing {
            page: [100., 100.],
            layers: vec![],
            link: None,
            text: vec![],
            annotations: vec![],
            sheet,
            problems: vec![],
            spec: None,
        };
        let located = concentric_on_drawing(&a, &drawing);
        let axes = located
            .get("f:hole_pattern:1")
            .expect("rounded centre still concentric");
        assert_eq!(axes.len(), 2);
        for (k, parameter) in [
            "location.length.x",
            "location.length.y",
            "location.length.z",
        ]
        .iter()
        .enumerate()
        {
            assert_eq!(
                axes.contains(parameter),
                k != deep,
                "view axis {deep}, {parameter}"
            );
        }
        // The renderer positions both pitch-circle ink and its mark using the
        // rounded recognition centre, not the exact cylinder axis.
        for point in drawing.sheet.items[0].lines.iter_mut().flatten() {
            point[0] += 0.0005;
            point[1] += 0.0005;
        }
        drawing
            .sheet
            .centre_marks
            .insert("end".into(), vec![[0.0005, 0.0005]]);
        assert_eq!(concentric_on_drawing(&a, &drawing), located);
        let mut misplaced = drawing.clone();
        for point in misplaced.sheet.items[0].lines.iter_mut().flatten() {
            point[0] += 0.002;
        }
        assert!(
            concentric_on_drawing(&a, &misplaced).is_empty(),
            "wrong drawn centres cannot borrow the model axis"
        );
        a.recognition.as_mut().unwrap().patterns[0]
            .parameters
            .insert("center".into(), json!([0.002, 0.002, 0.002]));
        assert!(
            concentric_on_drawing(&a, &drawing).is_empty(),
            "an offset beyond model tolerance cannot share the axis"
        );
    }
}
