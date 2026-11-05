//! draftwright's text against build123d's `Text` (`tools/capture_text.py`): every sample label
//! in both of draftwright's fonts must have build123d's outline (within `common::TOL` both ways),
//! bounding box and area.

#[path = "common/text.rs"]
mod common;

#[test]
fn text_matches_build123d() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/text.json.gz");
    let mut problems = Vec::new();
    for case in common::load(&path)["cases"].as_array().unwrap() {
        let s = common::compare(case);
        if s.outline.0 < 0.999
            || s.outline.1 < 0.999
            || s.box_error > 5e-5
            || ((s.area.1 - s.area.0) / s.area.0).abs() > 2e-4
        {
            problems.push(format!(
                "{} {:?}: box {:.1e} outline {:.4} {:.4} area {} {}",
                case["font"],
                case["text"],
                s.box_error,
                s.outline.0,
                s.outline.1,
                s.area.0,
                s.area.1
            ));
        }
    }
    assert!(problems.is_empty(), "{}", problems.join("\n"));
}

#[test]
fn diameter_fallback_uses_the_supported_outline_and_advance_in_both_fonts() {
    for bytes in [
        draftwright::api::MONO,
        include_bytes!("../fonts/IBMPlexSansCondensed-Regular.ttf").as_slice(),
    ] {
        let raw = ttf_parser::Face::parse(bytes, 0).unwrap();
        assert!(
            raw.glyph_index('⌀').is_none(),
            "the bundled font needs the explicit fallback"
        );
        assert!(raw.glyph_index('ø').is_some());
        let font = draftwright::text::Font::new(bytes.to_vec()).unwrap();
        assert!(font.supports('⌀'));
        for text in ["⌀80", "CONCENTRIC WITH ⌀80", "⌀30 THRU", "ø80 / ⌀30"] {
            let expected = text.replace('⌀', "ø");
            let a = font.baseline(text, 3.0);
            let b = font.baseline(&expected, 3.0);
            assert_eq!(a.contours, b.contours);
            assert_eq!((a.min, a.max), (b.min, b.max));
            assert_eq!(font.advance(text, 3.0), font.advance(&expected, 3.0));
        }
    }
}
