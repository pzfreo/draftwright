//! Pure ISO metric designation parsing and coarse pitches, independent of authoring or drawing state.

/// ISO 261's coarse pitches by nominal size, for a designation that states none.
pub(crate) const COARSE: [(f64, f64); 40] = [
    (1.0, 0.25),
    (1.1, 0.25),
    (1.2, 0.25),
    (1.4, 0.3),
    (1.6, 0.35),
    (1.8, 0.35),
    (2.0, 0.4),
    (2.2, 0.45),
    (2.5, 0.45),
    (3.0, 0.5),
    (3.5, 0.6),
    (4.0, 0.7),
    (4.5, 0.75),
    (5.0, 0.8),
    (6.0, 1.0),
    (7.0, 1.0),
    (8.0, 1.25),
    (9.0, 1.25),
    (10.0, 1.5),
    (11.0, 1.5),
    (12.0, 1.75),
    (14.0, 2.0),
    (16.0, 2.0),
    (18.0, 2.5),
    (20.0, 2.5),
    (22.0, 2.5),
    (24.0, 3.0),
    (27.0, 3.0),
    (30.0, 3.5),
    (33.0, 3.5),
    (36.0, 4.0),
    (39.0, 4.0),
    (42.0, 4.5),
    (45.0, 4.5),
    (48.0, 5.0),
    (52.0, 5.0),
    (56.0, 5.5),
    (60.0, 5.5),
    (64.0, 6.0),
    (68.0, 6.0),
];

/// A metric designation's nominal size and pitch (the coarse pitch when it states none), or why
/// it is not one: `M<size>`, then optionally `x<pitch>` (spaces allowed around the `x`), `-<class>`
/// and ` LH` or ` RH`.
pub fn metric(designation: &str) -> Result<(f64, f64), String> {
    let bad = || {
        format!(
            "{designation:?} is not an ISO metric thread designation: M<size>[x<pitch>][-<class>][ LH] (M6, M10x1.25-6g)"
        )
    };
    let s = designation.trim();
    let rest = s.strip_prefix('M').ok_or_else(bad)?;
    let number = |t: &str| -> Option<(f64, usize)> {
        let n = t
            .find(|c: char| !(c.is_ascii_digit() || c == '.'))
            .unwrap_or(t.len());
        let v: f64 = t[..n].parse().ok()?;
        (v.is_finite() && v > 0.0).then_some((v, n))
    };
    let (nominal, n) = number(rest).ok_or_else(bad)?;
    let mut rest = &rest[n..];
    let mut pitch = None;
    let trimmed = rest.trim_start();
    if let Some(after) = trimmed.strip_prefix(['x', 'X', '×']) {
        let (p, n) = number(after.trim_start()).ok_or_else(bad)?;
        pitch = Some(p);
        rest = &after.trim_start()[n..];
    }
    if let Some(after) = rest.strip_prefix('-') {
        let n = after
            .find(|c: char| !c.is_ascii_alphanumeric())
            .unwrap_or(after.len());
        if n == 0 {
            return Err(bad());
        }
        rest = &after[n..];
    }
    let hand = rest.trim();
    if !(hand.is_empty() || (matches!(hand, "LH" | "RH") && rest.starts_with(char::is_whitespace)))
    {
        return Err(bad());
    }
    let pitch = match pitch {
        Some(p) => p,
        None => COARSE
            .iter()
            .find(|(d, _)| (d - nominal).abs() < 1e-9)
            .map(|(_, p)| *p)
            .ok_or_else(|| {
                format!(
                    "{designation:?} states no pitch and M{} has no ISO 261 coarse pitch; write it (M{}x<pitch>)",
                    crate::pmi::fmt_g3(nominal),
                    crate::pmi::fmt_g3(nominal)
                )
            })?,
    };
    Ok((nominal, pitch))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn metric_designations_require_a_whole_input_and_preserve_pitch() {
        for (text, expected) in [
            ("M10", (10.0, 1.5)),
            ("M10-6g", (10.0, 1.5)),
            ("M10 x 1.25-6g LH", (10.0, 1.25)),
            ("M6×1-6H RH", (6.0, 1.0)),
            ("M9-6g", (9.0, 1.25)),
            ("M11-6g", (11.0, 1.5)),
        ] {
            assert_eq!(metric(text).unwrap(), expected, "{text}");
        }
        for text in [
            "10",
            "NOTE M10-6g",
            "M10junk",
            "M0",
            "M10x0",
            "M10-",
            "M10LH",
        ] {
            assert!(metric(text).is_err(), "{text}");
        }
    }
}
