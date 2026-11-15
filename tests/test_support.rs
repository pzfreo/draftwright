#[path = "common/support.rs"]
mod support;

#[test]
fn every_pmi_fixture_has_a_scheduled_case() {
    let mut actual: Vec<_> = std::fs::read_dir(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/pmi/step"
    ))
    .unwrap()
    .map(|e| e.unwrap().file_name().into_string().unwrap())
    .filter_map(|name| name.strip_suffix(".gz").map(str::to_owned))
    .collect();
    actual.sort();
    let mut scheduled = support::PMI_FILES.to_vec();
    scheduled.push(support::DERIVED_PMI_FILE);
    scheduled.sort();
    assert_eq!(actual, scheduled);
}

use crate::cache;

fn plate() -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/plate.step")
}

#[test]
fn cached_documents_do_not_share_edits_and_key_the_whole_analysis() {
    let (part, mut first) = cache::native(&plate());
    let (same_part, second) = cache::native(&plate());
    assert!(std::sync::Arc::ptr_eq(&part, &same_part));
    let original = serde_json::to_value(&second).unwrap();
    first.binding.part.push_str(" edited");
    assert_eq!(serde_json::to_value(&second).unwrap(), original);

    let mut plan = cache::default_plan(&second, false);
    let expected = serde_json::to_value(&plan).unwrap();
    plan.dimensions.clear();
    assert_eq!(
        serde_json::to_value(cache::default_plan(&second, false)).unwrap(),
        expected
    );
    // Same binding/hash, different projection evidence: never reuse the first plan.
    let mut edited = second.clone();
    edited.edge_ink = None;
    let fresh = draftwright::model::plan(&edited, &Default::default()).unwrap();
    assert_eq!(
        serde_json::to_value(cache::default_plan(&edited, false)).unwrap(),
        serde_json::to_value(fresh).unwrap()
    );
    let recovered = draftwright::model::plan_recovered(&second, &Default::default()).unwrap();
    assert_eq!(
        serde_json::to_value(cache::default_plan(&second, true)).unwrap(),
        serde_json::to_value(recovered).unwrap()
    );
}

#[test]
fn cache_initialises_an_input_once_under_contention() {
    let path = plate();
    let bytes = std::fs::read(&path).unwrap();
    let calls = std::sync::atomic::AtomicUsize::new(0);
    let results = support::parallel(&[0; 8], |_| {
        cache::analysed("single-flight-plate.step", &bytes, true, || {
            calls.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            draftwright::model::analyse_native(&path).unwrap()
        })
    });
    assert_eq!(calls.load(std::sync::atomic::Ordering::Relaxed), 1);
    assert!(
        results
            .iter()
            .all(|(part, _)| std::sync::Arc::ptr_eq(part, &results[0].0))
    );
}

#[test]
fn bounded_workers_preserve_input_order_and_propagate_panics() {
    assert_eq!(support::parallel(&[3, 1, 2], |i| i * 2), [6, 2, 4]);
    assert!(support::parallel::<i32, i32>(&[], |i| *i).is_empty());
    assert!(
        std::panic::catch_unwind(|| support::parallel(&[1, 2, 3], |i| {
            assert_ne!(*i, 2, "worker assertion must reach the test harness");
        }))
        .is_err()
    );
}
