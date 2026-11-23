/// Printed alternatives name a representative and all dependencies; intrinsic
/// ownership remains an exclusion even when its owner's dimension is omitted.
pub fn conveyed_owners(basis: &str) -> Vec<&str> {
    let Some((owner, why)) = basis
        .strip_prefix("conveyed by ")
        .and_then(|rest| rest.split_once(": "))
    else {
        return Vec::new();
    };
    if why.starts_with("recognised ownership") {
        return Vec::new();
    }
    why.split_once("; dependencies: ")
        .map_or_else(|| vec![owner], |(_, ids)| ids.split(", ").collect())
}
