//! The chain-evaluation core: which approval chain and band govern an order.
//!
//! A line-for-line twin of `backend/inventory/cost_center_chain_core.py`, which
//! the Python decision path (`po_approval_service`, still Python) uses to apply
//! the same rules. Two implementations of one rule is exactly what the
//! differential test exists for: `tests/contract/chain_fixtures.json` is
//! written FROM the Python module and replayed here by `fixtures_agree`, and
//! `tests/contract/chain_differential.py` seeds random cases into a database
//! and compares this module (through `POST /approval-chains/evaluate`) with the
//! Python function. Change a rule in one file and the other tests go red.
//!
//! No I/O, no clock. FAIL CLOSED: once any chain is active, an order whose
//! center is unknown or inactive, that no chain covers, whose chain is
//! malformed or whose value is unknown is `unresolved`, never `not_required`.

use std::collections::{HashMap, HashSet};

use serde_json::{json, Value};

pub const EPS: f64 = 0.005;
pub const MAX_LEVELS: usize = 5;
pub const MAX_USERS_PER_LEVEL: usize = 20;
pub const MAX_BANDS: usize = 10;
pub const MAX_DEPTH: usize = 32;

pub const NOT_REQUIRED: &str = "not_required";
pub const REQUIRED: &str = "required";
pub const UNRESOLVED: &str = "unresolved";

/// "analyst" (1) or "admin" (2): a role level accepts that role or above.
pub fn role_rank(role: &str) -> Option<u8> {
    match role {
        "analyst" => Some(1),
        "admin" => Some(2),
        _ => None,
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Level {
    Role(String),
    /// Sorted and de-duplicated.
    Users(Vec<String>),
}

impl Level {
    pub fn to_json(&self) -> Value {
        match self {
            Level::Role(r) => json!({"kind": "role", "role": r}),
            Level::Users(ids) => json!({"kind": "users", "user_ids": ids}),
        }
    }

    pub fn text(&self) -> String {
        match self {
            Level::Role(r) => format!("role:{r}"),
            Level::Users(ids) => format!("users:{}", ids.join(",")),
        }
    }
}

/// A level in canonical form, or None when it is malformed.
pub fn normalize_level(raw: &Value) -> Option<Level> {
    let obj = raw.as_object()?;
    match obj.get("kind").and_then(Value::as_str) {
        Some("role") => {
            let role = obj.get("role")?.as_str()?;
            role_rank(role).map(|_| Level::Role(role.to_string()))
        }
        Some("users") => {
            let ids = obj.get("user_ids")?.as_array()?;
            if ids.is_empty() || ids.len() > MAX_USERS_PER_LEVEL {
                return None;
            }
            let mut out: Vec<String> = Vec::with_capacity(ids.len());
            for i in ids {
                let s = i.as_str()?;
                if s.is_empty() {
                    return None;
                }
                out.push(s.to_string());
            }
            out.sort();
            out.dedup();
            Some(Level::Users(out))
        }
        _ => None,
    }
}

pub fn normalize_levels(raw: &Value) -> Option<Vec<Level>> {
    let items = raw.as_array()?;
    if items.is_empty() || items.len() > MAX_LEVELS {
        return None;
    }
    items.iter().map(normalize_level).collect()
}

pub fn fingerprint(chain_id: &str, min_amount: f64, levels: &[Level]) -> String {
    let texts: Vec<String> = levels.iter().map(Level::text).collect();
    format!("{chain_id}|{min_amount:.2}|{}", texts.join(";"))
}

#[derive(Debug, Clone)]
pub struct Center {
    pub id: String,
    pub parent_id: Option<String>,
    pub active: bool,
}

/// `bands` is the raw JSON as stored: `[{min_amount, levels}]`.
#[derive(Debug, Clone)]
pub struct Chain {
    pub id: String,
    pub cost_center_id: Option<String>,
    pub active: bool,
    pub bands: Value,
}

#[derive(Debug, Clone)]
pub struct Band {
    pub min_amount: f64,
    pub levels: Vec<Level>,
}

/// The chain's bands sorted ascending, or None when ANY band is malformed.
pub fn valid_bands(chain: &Chain) -> Option<Vec<Band>> {
    let raw = chain.bands.as_array()?;
    if raw.is_empty() || raw.len() > MAX_BANDS {
        return None;
    }
    let mut bands: Vec<Band> = Vec::with_capacity(raw.len());
    for b in raw {
        let obj = b.as_object()?;
        let m = match obj.get("min_amount")? {
            Value::Number(n) => n.as_f64()?,
            _ => return None,
        };
        if !m.is_finite() || m < 0.0 || bands.iter().any(|x| x.min_amount == m) {
            return None;
        }
        let levels = normalize_levels(obj.get("levels")?)?;
        bands.push(Band { min_amount: m, levels });
    }
    bands.sort_by(|a, b| a.min_amount.partial_cmp(&b.min_amount).unwrap());
    Some(bands)
}

/// (chain, unresolved reason).
pub fn find_chain<'a>(
    chains: &'a [Chain],
    centers: &[Center],
    center_id: Option<&str>,
) -> (Option<&'a Chain>, Option<&'static str>) {
    let active: Vec<&Chain> = chains.iter().filter(|c| c.active).collect();
    let default = active.iter().find(|c| c.cost_center_id.is_none()).copied();
    let Some(center_id) = center_id else {
        return match default {
            Some(c) => (Some(c), None),
            None => (None, Some("no_cost_center")),
        };
    };
    let by_id: HashMap<&str, &Center> = centers.iter().map(|c| (c.id.as_str(), c)).collect();
    let mut node = by_id.get(center_id).copied();
    match node {
        Some(n) if n.active => {}
        _ => return (None, Some("cost_center_invalid")),
    }
    let mut visited: HashSet<&str> = HashSet::new();
    let mut depth = 0usize;
    while let Some(n) = node {
        if visited.contains(n.id.as_str()) || depth > MAX_DEPTH {
            return (None, Some("cost_center_invalid"));
        }
        visited.insert(n.id.as_str());
        if let Some(c) = active.iter().find(|c| c.cost_center_id.as_deref() == Some(n.id.as_str())) {
            return (Some(*c), None);
        }
        let parent = n.parent_id.as_deref().filter(|p| !p.is_empty());
        node = parent.and_then(|p| by_id.get(p).copied());
        depth += 1;
        if parent.is_some() && node.is_none() {
            return (None, Some("cost_center_invalid"));
        }
    }
    match default {
        Some(c) => (Some(c), None),
        None => (None, Some("no_chain")),
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Resolution {
    pub state: &'static str,
    pub reason: Option<&'static str>,
    pub chain_id: Option<String>,
    pub min_amount: Option<f64>,
    pub levels: Option<Vec<Level>>,
    pub fingerprint: Option<String>,
    pub escalated: bool,
    pub cost_center_id: Option<String>,
}

impl Resolution {
    fn bare(state: &'static str, center: Option<&str>) -> Self {
        Resolution {
            state, reason: None, chain_id: None, min_amount: None, levels: None,
            fingerprint: None, escalated: false, cost_center_id: center.map(String::from),
        }
    }

    fn unresolved(reason: &'static str, center: Option<&str>, chain_id: Option<&str>) -> Self {
        Resolution { reason: Some(reason), chain_id: chain_id.map(String::from),
            ..Resolution::bare(UNRESOLVED, center) }
    }

    /// The same keys, in the same order, as `core.resolve`'s dict.
    pub fn to_json(&self) -> Value {
        json!({
            "state": self.state,
            "reason": self.reason,
            "chain_id": self.chain_id,
            "min_amount": self.min_amount,
            "levels": self.levels.as_ref().map(|l| l.iter().map(Level::to_json).collect::<Vec<_>>()),
            "fingerprint": self.fingerprint,
            "escalated": self.escalated,
            "cost_center_id": self.cost_center_id,
        })
    }
}

/// What approval chain means for one order. See the Python twin.
pub fn resolve(
    chains: &[Chain],
    centers: &[Center],
    center_id: Option<&str>,
    amount: Option<f64>,
    escalate: bool,
) -> Resolution {
    if !chains.iter().any(|c| c.active) {
        return Resolution::bare(NOT_REQUIRED, center_id);
    }
    let (chain, why) = find_chain(chains, centers, center_id);
    let Some(chain) = chain else {
        return Resolution::unresolved(why.unwrap_or("no_chain"), center_id, None);
    };
    let Some(bands) = valid_bands(chain) else {
        return Resolution::unresolved("chain_invalid", center_id, Some(&chain.id));
    };
    let amount = match amount {
        Some(a) if a.is_finite() => a,
        _ => return Resolution::unresolved("amount_unknown", center_id, Some(&chain.id)),
    };
    let band = if escalate {
        bands.last().unwrap()
    } else {
        match bands.iter().rev().find(|b| amount + EPS >= b.min_amount) {
            Some(b) => b,
            None => {
                return Resolution {
                    chain_id: Some(chain.id.clone()),
                    ..Resolution::bare(NOT_REQUIRED, center_id)
                }
            }
        }
    };
    Resolution {
        state: REQUIRED,
        reason: None,
        chain_id: Some(chain.id.clone()),
        min_amount: Some(band.min_amount),
        levels: Some(band.levels.clone()),
        fingerprint: Some(fingerprint(&chain.id, band.min_amount, &band.levels)),
        escalated: escalate,
        cost_center_id: center_id.map(String::from),
    }
}

/// The center and every center below it (cycle-safe, depth-capped), sorted.
pub fn descendants(centers: &[Center], center_id: &str) -> Vec<String> {
    let mut kids: HashMap<&str, Vec<&str>> = HashMap::new();
    for c in centers {
        if let Some(p) = c.parent_id.as_deref().filter(|p| !p.is_empty()) {
            kids.entry(p).or_default().push(c.id.as_str());
        }
    }
    let mut out: Vec<String> = Vec::new();
    let mut stack: Vec<(&str, usize)> = vec![(center_id, 0)];
    let mut seen: HashSet<&str> = HashSet::new();
    while let Some((cid, depth)) = stack.pop() {
        if seen.contains(cid) || depth > MAX_DEPTH {
            continue;
        }
        seen.insert(cid);
        out.push(cid.to_string());
        let mut ks = kids.get(cid).cloned().unwrap_or_default();
        ks.sort_unstable();
        for k in ks {
            stack.push((k, depth + 1));
        }
    }
    out.sort();
    out
}

/// Would setting `parent` as `center`'s parent make a cycle or a tree deeper
/// than [`MAX_DEPTH`]? (Management-side guard; the evaluator also survives a
/// cycle that got into the table some other way.)
pub fn reparent_problem(centers: &[Center], center_id: &str, parent: &str) -> Option<&'static str> {
    if parent == center_id || descendants(centers, center_id).iter().any(|d| d == parent) {
        return Some("cycle");
    }
    let by_id: HashMap<&str, &Center> = centers.iter().map(|c| (c.id.as_str(), c)).collect();
    let mut depth_above = 0usize;
    let mut cur = Some(parent);
    while let Some(c) = cur {
        depth_above += 1;
        if depth_above > MAX_DEPTH {
            return Some("too_deep");
        }
        cur = by_id.get(c).and_then(|n| n.parent_id.as_deref()).filter(|p| !p.is_empty());
    }
    // the subtree under `center_id` also counts
    let mut deepest_below = 0usize;
    let mut level: Vec<&str> = vec![center_id];
    let kids_of = |id: &str| -> Vec<&str> {
        centers.iter().filter(|c| c.parent_id.as_deref() == Some(id)).map(|c| c.id.as_str()).collect()
    };
    let mut guard = 0;
    while !level.is_empty() && guard <= MAX_DEPTH + 1 {
        let mut next = Vec::new();
        for id in &level {
            next.extend(kids_of(id));
        }
        if !next.is_empty() {
            deepest_below += 1;
        }
        level = next;
        guard += 1;
    }
    if depth_above + 1 + deepest_below > MAX_DEPTH {
        return Some("too_deep");
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn center(id: &str, parent: Option<&str>, active: bool) -> Center {
        Center { id: id.into(), parent_id: parent.map(String::from), active }
    }

    fn chain(id: &str, center: Option<&str>, bands: Value) -> Chain {
        Chain { id: id.into(), cost_center_id: center.map(String::from), active: true, bands }
    }

    fn centers() -> Vec<Center> {
        vec![center("root", None, true), center("ops", Some("root"), true),
             center("ops-north", Some("ops"), true), center("retired", Some("root"), false)]
    }

    fn default_chain() -> Chain {
        chain("c-default", None, json!([
            {"min_amount": 1000, "levels": [{"kind": "role", "role": "analyst"}]},
            {"min_amount": 10000, "levels": [{"kind": "role", "role": "analyst"}, {"kind": "role", "role": "admin"}]},
        ]))
    }

    fn ops_chain() -> Chain {
        chain("c-ops", Some("ops"), json!([{"min_amount": 500, "levels": [{"kind": "role", "role": "admin"}]}]))
    }

    #[test]
    fn no_active_chain_changes_nothing() {
        let r = resolve(&[], &centers(), Some("ops"), Some(99999.0), false);
        assert_eq!(r.state, NOT_REQUIRED);
        let mut inactive = default_chain();
        inactive.active = false;
        assert_eq!(resolve(&[inactive], &centers(), None, None, false).state, NOT_REQUIRED);
    }

    #[test]
    fn bands_are_inclusive_with_the_shared_tolerance() {
        let c = [default_chain()];
        assert_eq!(resolve(&c, &centers(), None, Some(999.0), false).state, NOT_REQUIRED);
        assert_eq!(resolve(&c, &centers(), None, Some(999.996), false).state, REQUIRED);
        assert_eq!(resolve(&c, &centers(), None, Some(1000.0), false).levels.unwrap().len(), 1);
        assert_eq!(resolve(&c, &centers(), None, Some(9999.0), false).levels.unwrap().len(), 1);
        assert_eq!(resolve(&c, &centers(), None, Some(10000.0), false).levels.unwrap().len(), 2);
    }

    #[test]
    fn a_center_uses_its_chain_then_an_ancestors_then_the_default() {
        let c = [default_chain(), ops_chain()];
        assert_eq!(resolve(&c, &centers(), Some("ops"), Some(600.0), false).chain_id.as_deref(), Some("c-ops"));
        assert_eq!(resolve(&c, &centers(), Some("ops-north"), Some(600.0), false).chain_id.as_deref(), Some("c-ops"));
        assert_eq!(resolve(&c, &centers(), Some("root"), Some(2000.0), false).chain_id.as_deref(), Some("c-default"));
    }

    #[test]
    fn fail_closed_reasons() {
        let reason = |chains: &[Chain], center: Option<&str>, amount: Option<f64>| {
            let r = resolve(chains, &centers(), center, amount, false);
            assert_eq!(r.state, UNRESOLVED);
            r.reason.unwrap()
        };
        assert_eq!(reason(&[ops_chain()], None, Some(5000.0)), "no_cost_center");
        assert_eq!(reason(&[ops_chain()], Some("root"), Some(5000.0)), "no_chain");
        assert_eq!(reason(&[default_chain()], Some("ghost"), Some(5000.0)), "cost_center_invalid");
        assert_eq!(reason(&[default_chain()], Some("retired"), Some(5000.0)), "cost_center_invalid");
        assert_eq!(reason(&[default_chain()], Some("ops"), None), "amount_unknown");
        assert_eq!(reason(&[default_chain()], Some("ops"), Some(f64::NAN)), "amount_unknown");
        // a value below every band is still unresolved when the center is
        assert_eq!(reason(&[ops_chain()], None, Some(1.0)), "no_cost_center");
    }

    #[test]
    fn a_malformed_chain_is_never_trusted() {
        let bad = [
            json!([]),
            json!([{"min_amount": 100, "levels": []}]),
            json!([{"min_amount": 100, "levels": [{"kind": "role", "role": "viewer"}]}]),
            json!([{"min_amount": 100, "levels": [{"kind": "users", "user_ids": []}]}]),
            json!([{"min_amount": 100, "levels": [{"kind": "users", "user_ids": [1]}]}]),
            json!([{"min_amount": 100, "levels": [{"kind": "other"}]}]),
            json!([{"min_amount": 100, "levels": vec![json!({"kind": "role", "role": "admin"}); 6]}]),
            json!([{"min_amount": 100, "levels": [{"kind": "role", "role": "admin"}]},
                   {"min_amount": 100, "levels": [{"kind": "role", "role": "analyst"}]}]),
            json!([{"min_amount": -1, "levels": [{"kind": "role", "role": "admin"}]}]),
            json!([{"min_amount": "5", "levels": [{"kind": "role", "role": "admin"}]}]),
            json!([{"min_amount": true, "levels": [{"kind": "role", "role": "admin"}]}]),
            json!("garbage"),
        ];
        for bands in bad {
            let r = resolve(&[chain("bad", None, bands.clone())], &centers(), None, Some(5000.0), false);
            assert_eq!((r.state, r.reason), (UNRESOLVED, Some("chain_invalid")), "{bands}");
        }
    }

    #[test]
    fn one_broken_band_breaks_the_whole_chain() {
        let c = chain("c", None, json!([
            {"min_amount": 100, "levels": [{"kind": "role", "role": "admin"}]},
            {"min_amount": 200, "levels": [{"kind": "role", "role": "root"}]}]));
        assert_eq!(resolve(&[c], &centers(), None, Some(150.0), false).reason, Some("chain_invalid"));
    }

    #[test]
    fn a_cycle_in_the_tree_fails_closed() {
        let cyc = vec![center("a", Some("b"), true), center("b", Some("a"), true)];
        let r = resolve(&[chain("c", Some("zzz"), json!([{"min_amount": 1, "levels": [{"kind": "role", "role": "admin"}]}]))],
            &cyc, Some("a"), Some(5.0), false);
        assert_eq!((r.state, r.reason), (UNRESOLVED, Some("cost_center_invalid")));
        assert_eq!(descendants(&cyc, "a"), vec!["a".to_string(), "b".to_string()]);
    }

    #[test]
    fn escalation_takes_the_top_band_but_never_rescues_an_unresolved_order() {
        let r = resolve(&[default_chain()], &centers(), None, Some(5.0), true);
        assert_eq!((r.state, r.levels.unwrap().len(), r.escalated), (REQUIRED, 2, true));
        assert_eq!(resolve(&[ops_chain()], &centers(), None, Some(5.0), true).state, UNRESOLVED);
    }

    #[test]
    fn fingerprints_match_the_python_format() {
        let r = resolve(&[default_chain()], &centers(), None, Some(2000.0), false);
        assert_eq!(r.fingerprint.as_deref(), Some("c-default|1000.00|role:analyst"));
        let named = chain("n", None, json!([{"min_amount": 1, "levels": [{"kind": "users", "user_ids": ["b", "a", "a"]}]}]));
        let r = resolve(&[named], &[], None, Some(5.0), false);
        assert_eq!(r.fingerprint.as_deref(), Some("n|1.00|users:a,b"));
    }

    #[test]
    fn descendants_include_self() {
        assert_eq!(descendants(&centers(), "ops"), vec!["ops", "ops-north"]);
        assert_eq!(descendants(&centers(), "root").len(), 4);
    }

    #[test]
    fn reparenting_refuses_cycles_and_absurd_depth() {
        let cs = centers();
        assert_eq!(reparent_problem(&cs, "ops", "ops-north"), Some("cycle"));
        assert_eq!(reparent_problem(&cs, "ops", "ops"), Some("cycle"));
        assert_eq!(reparent_problem(&cs, "ops-north", "root"), None);
        let mut deep = vec![center("n0", None, true)];
        for i in 1..=MAX_DEPTH {
            deep.push(center(&format!("n{i}"), Some(&format!("n{}", i - 1)), true));
        }
        deep.push(center("loose", None, true));
        assert_eq!(reparent_problem(&deep, "loose", &format!("n{MAX_DEPTH}")), Some("too_deep"));
        assert_eq!(reparent_problem(&deep, "loose", "n3"), None);
    }

    /// The differential half that needs no database: every case in
    /// `tests/contract/chain_fixtures.json` was answered by the PYTHON module;
    /// this module must give the identical answer.
    #[test]
    fn fixtures_agree_with_python() {
        let raw = include_str!("../../tests/contract/chain_fixtures.json");
        let doc: Value = serde_json::from_str(raw).expect("fixtures parse");
        let cases = doc["cases"].as_array().expect("cases");
        assert!(cases.len() >= 500, "the fixture set was cut down: {}", cases.len());
        let mut required = 0;
        for (i, case) in cases.iter().enumerate() {
            let chains: Vec<Chain> = case["chains"].as_array().unwrap().iter().map(|c| Chain {
                id: c["id"].as_str().unwrap().into(),
                cost_center_id: c["cost_center_id"].as_str().map(String::from),
                active: c["active"].as_bool().unwrap(),
                bands: c["bands"].clone(),
            }).collect();
            let centers: Vec<Center> = case["centers"].as_array().unwrap().iter().map(|c| Center {
                id: c["id"].as_str().unwrap().into(),
                parent_id: c["parent_id"].as_str().map(String::from),
                active: c["active"].as_bool().unwrap(),
            }).collect();
            let got = resolve(&chains, &centers, case["center_id"].as_str(),
                case["amount"].as_f64(), case["escalate"].as_bool().unwrap()).to_json();
            assert_eq!(got, case["expected"], "case {i}: {}", case["label"]);
            if got["state"] == REQUIRED {
                required += 1;
            }
        }
        assert!(required > 50, "too few REQUIRED cases to mean anything: {required}");
    }
}
