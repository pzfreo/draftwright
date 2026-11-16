`plate-datum-a.step.gz` and `prismatic-datum-a.step.gz` are the respective
`../parts/` STEP fixtures with one semantic datum A on face 0.
It was written with haecceity's PMI writer (`Mode::Replace`,
`PresentationPolicy::RemovePresentation`), one `Feature::Items` face anchor and
`Datum::new("A", Some(FeatureId(0)), [])`. The original B-rep is retained.
The browser parity test reads these supplied STEP bytes; the engine never authors STEP.
