//! Business vocabulary and independent input validation. No target selectors.
use crate::Fault;
use regex::Regex;
use rust_decimal::Decimal;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::collections::{HashSet, VecDeque};
use std::str::FromStr;

#[derive(Clone, Debug)]
pub struct Model {
    pub document: Value,
    pub graph: Value,
    pub business: Value,
    pub states: Map<String, Value>,
    pub edges: Map<String, Value>,
    pub data_sets: Map<String, Value>,
    pub digest: String,
}

pub fn business(element: &Value) -> &Value {
    &element["properties"]["business"]
}
pub fn string(value: &Value) -> &str {
    value.as_str().unwrap_or("")
}
fn text(value: &Value) -> Result<(), Fault> {
    let s = string(value);
    if s.trim().is_empty()
        || s.chars().count() > 4000
        || ["{{", "querySelector", "xpath=", "css=", "document.", "=>"]
            .iter()
            .any(|v| s.contains(v))
    {
        return Err(Fault::unknown(
            "Invalid business model: use nonempty business prose without selectors or code",
        ));
    }
    Ok(())
}
fn rules(value: &Value) -> Result<(), Fault> {
    let array = value
        .as_array()
        .filter(|a| !a.is_empty() && a.len() <= 30)
        .ok_or_else(|| Fault::unknown("Invalid business model: supply 1–30 rules"))?;
    for rule in array {
        text(rule)?;
    }
    Ok(())
}
fn shape(value: &Value, allowed: &[&str], required: &[&str]) -> Result<(), Fault> {
    let object = value
        .as_object()
        .ok_or_else(|| Fault::unknown("Invalid business model: expected an object"))?;
    if object.keys().any(|k| !allowed.contains(&k.as_str()))
        || required.iter().any(|k| !object.contains_key(*k))
    {
        return Err(Fault::unknown(
            "Invalid business model: unsupported or missing business fields",
        ));
    }
    Ok(())
}

fn empty_actions(value: &Value) -> bool {
    value.is_null() || value.as_array().is_some_and(Vec::is_empty)
}

impl Model {
    pub fn new(mut document: Value) -> Result<Self, Fault> {
        if !document.is_object() {
            return Err(Fault::unknown("Supply a GraphWalker model object"));
        }
        let models = document["models"]
            .as_array_mut()
            .filter(|a| a.len() == 1)
            .ok_or_else(|| Fault::unknown("Supply exactly one GraphWalker business graph"))?;
        let graph = &mut models[0];
        for key in ["id", "name", "startElementId"] {
            text(&graph[key])?;
        }
        if !empty_actions(&graph["actions"]) {
            return Err(Fault::unknown(
                "Native action scripts are outside the business vocabulary",
            ));
        }
        shape(&graph["properties"], &["business", "rpc"], &["business"])?;
        // Transport metadata belongs to its target adapter. The core preserves it
        // in fingerprints and callbacks, validating only the profile boundary.
        let rpc_profile = graph["properties"].get("rpc").is_some();
        if rpc_profile
            && (!graph["properties"]["rpc"].is_object()
                || graph["properties"]["rpc"]["version"].as_u64() != Some(1))
        {
            return Err(Fault::unknown(
                "RPC metadata must use an object with version 1",
            ));
        }
        let spec = &mut graph["properties"]["business"];
        shape(
            spec,
            &[
                "version",
                "purpose",
                "entry path",
                "rules",
                "data sets",
                "coverage",
            ],
            &["version", "purpose", "rules"],
        )?;
        if spec["version"] != 3 {
            return Err(Fault::unknown(
                "This runner accepts business model version 3",
            ));
        }
        text(&spec["purpose"])?;
        rules(&spec["rules"])?;
        if let Some(entry) = spec.get("entry path") {
            let path = entry
                .as_str()
                .ok_or_else(|| Fault::unknown("Entry path must be a relative path"))?;
            if path.is_empty()
                || path.starts_with("//")
                || path.contains("://")
                || path.contains('\\')
                || path.chars().any(char::is_control)
            {
                return Err(Fault::unknown(
                    "Entry path must stay relative to the target URL",
                ));
            }
        }
        if spec.get("coverage").is_none() {
            spec["coverage"] = json!({"edges":100,"states":100});
        }
        shape(
            &spec["coverage"],
            &["edges", "states"],
            &["edges", "states"],
        )?;
        for key in ["edges", "states"] {
            if !spec["coverage"][key]
                .as_f64()
                .is_some_and(|n| n.is_finite() && (0.0..=100.0).contains(&n))
            {
                return Err(Fault::unknown("Coverage must be between 0 and 100"));
            }
        }
        let data_sets = spec.get("data sets").cloned().unwrap_or(json!({}));
        let data_sets = data_sets
            .as_object()
            .filter(|d| d.len() <= 20)
            .ok_or_else(|| Fault::unknown("Supply at most 20 business data sets"))?
            .clone();
        for (name, fields) in &data_sets {
            text(&json!(name))?;
            let fields = fields
                .as_object()
                .filter(|f| !f.is_empty() && f.len() <= 20)
                .ok_or_else(|| Fault::unknown("Data sets need 1–20 fields"))?;
            validate_fields(fields)?;
        }
        if graph.get("generator").is_none() {
            graph["generator"] = json!("quick_random(edge_coverage(100))");
        }
        text(&graph["generator"])?;
        let mut states = Map::new();
        let mut edges = Map::new();
        let mut ids = HashSet::new();
        let mut names = HashSet::new();
        for (key, destination, limit) in
            [("vertices", &mut states, 100), ("edges", &mut edges, 500)]
        {
            let elements = graph[key]
                .as_array()
                .filter(|a| !a.is_empty() && a.len() <= limit)
                .ok_or_else(|| Fault::unknown("Supply 1–100 states and 1–500 journeys"))?;
            for element in elements {
                text(&element["id"])?;
                text(&element["name"])?;
                if !ids.insert(string(&element["id"]).to_owned())
                    || !names.insert(string(&element["name"]).to_owned())
                {
                    return Err(Fault::unknown("Duplicate graph ID or name"));
                }
                if element
                    .get("guard")
                    .is_some_and(|v| !v.is_null() && v != "")
                    || !empty_actions(&element["actions"])
                {
                    return Err(Fault::unknown(
                        "Business models cannot contain action or guard scripts",
                    ));
                }
                shape(&element["properties"], &["business", "rpc"], &["business"])?;
                if let Some(metadata) = element["properties"].get("rpc")
                    && (!rpc_profile || !metadata.is_object())
                {
                    return Err(Fault::unknown(
                        "Element RPC metadata requires an object and a graph RPC profile",
                    ));
                }
                let spec = business(element);
                if key == "vertices" {
                    shape(spec, &["description", "rules"], &["description", "rules"])?;
                    text(&spec["description"])?;
                    rules(&spec["rules"])?;
                } else {
                    shape(
                        spec,
                        &[
                            "intent",
                            "data set",
                            "example",
                            "accepted at",
                            "rejected at",
                        ],
                        &["intent"],
                    )?;
                    text(&spec["intent"])?;
                }
                destination.insert(string(&element["id"]).to_owned(), element.clone());
            }
        }
        if !states.contains_key(string(&graph["startElementId"])) {
            return Err(Fault::unknown("The graph must start at a state"));
        }
        let model = Self {
            graph: graph.clone(),
            business: business(graph).clone(),
            states,
            edges,
            data_sets,
            digest: format!("{:x}", Sha256::digest(canonical(&document).as_bytes())),
            document,
        };
        for edge in model.edges.values() {
            let spec = business(edge);
            if !model.states.contains_key(string(&edge["sourceVertexId"]))
                || !model.states.contains_key(string(&edge["targetVertexId"]))
            {
                return Err(Fault::unknown("Journey endpoints must exist"));
            }
            if spec.get("data set").is_some() {
                let fields = model.fields(edge)?;
                let accepted = spec.get("accepted at").unwrap_or(&edge["targetVertexId"]);
                if !model.states.contains_key(string(accepted))
                    || !model.states.contains_key(string(&spec["rejected at"]))
                    || accepted == &spec["rejected at"]
                {
                    return Err(Fault::unknown(
                        "A data journey needs distinct acceptance and rejection states",
                    ));
                }
                let example = model.example(edge)?;
                validate_input(fields, &example)?;
                let expected = if violations(fields, &example).is_empty() {
                    accepted
                } else {
                    &spec["rejected at"]
                };
                if expected != &edge["targetVertexId"] {
                    return Err(Fault::unknown(
                        "Scenario data contradicts the journey destination",
                    ));
                }
                model.setup(string(&edge["sourceVertexId"]))?;
            } else if ["example", "accepted at", "rejected at"]
                .iter()
                .any(|k| spec.get(k).is_some())
            {
                return Err(Fault::unknown("Data options require a data set"));
            }
        }
        let used = model
            .edges
            .values()
            .filter_map(|e| business(e)["data set"].as_str())
            .collect::<HashSet<_>>();
        if model
            .data_sets
            .keys()
            .any(|name| !used.contains(name.as_str()))
        {
            return Err(Fault::unknown(
                "Every declared data set must be used by a journey",
            ));
        }
        Ok(model)
    }
    pub fn fields(&self, edge: &Value) -> Result<&Map<String, Value>, Fault> {
        self.data_sets
            .get(string(&business(edge)["data set"]))
            .and_then(Value::as_object)
            .ok_or_else(|| Fault::unknown("Journey refers to an unknown data set"))
    }
    pub fn example(&self, edge: &Value) -> Result<Value, Fault> {
        if business(edge).get("data set").is_none() {
            return Ok(json!({}));
        }
        Ok(business(edge)
            .get("example")
            .cloned()
            .unwrap_or(baseline(self.fields(edge)?)))
    }
    pub fn start(&self) -> &str {
        string(&self.graph["startElementId"])
    }
    pub fn setup(&self, target: &str) -> Result<Vec<Value>, Fault> {
        let mut queue = VecDeque::from([(self.start().to_owned(), Vec::new())]);
        let mut seen = HashSet::new();
        while let Some((state, path)) = queue.pop_front() {
            if state == target {
                return Ok(path);
            }
            if !seen.insert(state.clone()) {
                continue;
            }
            for edge in self
                .edges
                .values()
                .filter(|e| string(&e["sourceVertexId"]) == state)
            {
                let mut next = path.clone();
                next.push(edge.clone());
                queue.push_back((string(&edge["targetVertexId"]).to_owned(), next));
            }
        }
        Err(Fault::unknown(format!(
            "No nominal setup path reaches {target}"
        )))
    }
}

pub fn literal(value: &Value) -> bool {
    (value.is_string() || value.is_number()) && rendered(value).chars().count() <= 2000
}
pub fn validate_input(fields: &Map<String, Value>, data: &Value) -> Result<(), Fault> {
    if !data.as_object().is_some_and(|d| {
        d.len() == fields.len() && fields.keys().all(|k| d.get(k).is_some_and(literal))
    }) {
        return Err(Fault::unknown(
            "Input must supply every declared field as literal text or numbers",
        ));
    }
    Ok(())
}
pub fn baseline(fields: &Map<String, Value>) -> Value {
    Value::Object(
        fields
            .iter()
            .map(|(k, v)| (k.clone(), v["example"].clone()))
            .collect(),
    )
}
pub fn rendered(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}
pub fn decimal(value: &Value) -> Option<Decimal> {
    Decimal::from_str(&rendered(value)).ok()
}
pub fn violations(fields: &Map<String, Value>, data: &Value) -> Vec<Value> {
    let email = Regex::new(r"^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$").unwrap();
    let number = Regex::new(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$").unwrap();
    let whole = Regex::new(r"^[+-]?\d+$").unwrap();
    let mut result = Vec::new();
    for (name, field) in fields {
        let raw = &data[name];
        let value = if raw.is_string() && field["trim spaces"].as_bool().unwrap_or(true) {
            json!(string(raw).trim())
        } else {
            raw.clone()
        };
        let mut broken = Vec::new();
        if value.is_null() || value == "" {
            if field["required"] == true {
                broken.push("required");
            }
        } else {
            match string(&field["type"]) {
                "text" | "email" => {
                    if let Some(s) = value.as_str() {
                        let len = s.chars().count() as u64;
                        if len < field["minimum length"].as_u64().unwrap_or(0) {
                            broken.push("too short");
                        }
                        if len > field["maximum length"].as_u64().unwrap_or(u64::MAX) {
                            broken.push("too long");
                        }
                        if field["type"] == "email" && !email.is_match(s) {
                            broken.push("valid email required");
                        }
                    } else {
                        broken.push("text required");
                    }
                }
                "choice" => {
                    if !field["options"]
                        .as_array()
                        .is_some_and(|a| a.contains(&value))
                    {
                        broken.push("must be one of the permitted choices");
                    }
                }
                _ => {
                    let s = rendered(&value);
                    if !number.is_match(&s) || value.is_boolean() {
                        broken.push("number required");
                    } else if let Some(n) = decimal(&value) {
                        if field["type"] == "whole number" && !whole.is_match(&s) {
                            broken.push("whole number required");
                        }
                        if decimal(&field["minimum"]).is_some_and(|a| n < a) {
                            broken.push("below minimum");
                        }
                        if decimal(&field["maximum"]).is_some_and(|a| n > a) {
                            broken.push("above maximum");
                        }
                    } else {
                        broken.push("finite number required");
                    }
                }
            }
        }
        if !broken.is_empty() {
            result.push(json!({"field":name,"violations":broken}));
        }
    }
    result
}

// Preserve the existing Python model fingerprint for old saved replay files.
fn canonical(v: &Value) -> String {
    match v {
        Value::Array(a) => format!(
            "[{}]",
            a.iter().map(canonical).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(o) => {
            let mut keys = o.keys().collect::<Vec<_>>();
            keys.sort();
            format!(
                "{{{}}}",
                keys.iter()
                    .map(|k| format!("{}: {}", json!(k), canonical(&o[*k])))
                    .collect::<Vec<_>>()
                    .join(", ")
            )
        }
        Value::Number(n) if n.is_f64() => {
            let f = n.as_f64().unwrap();
            let encoded = if f != 0.0 && (f.abs() < 0.0001 || f.abs() >= 1e16) {
                format!("{f:e}")
            } else {
                n.to_string()
            };
            if let Some((mantissa, exponent)) = encoded.split_once('e') {
                let exponent = exponent.parse::<i32>().unwrap();
                format!(
                    "{mantissa}e{}{abs:02}",
                    if exponent < 0 { "-" } else { "+" },
                    abs = exponent.abs()
                )
            } else {
                encoded
            }
        }
        _ => v.to_string(),
    }
}

/// Validate independent data domains before planning or generation.
pub fn validate_fields(fields: &Map<String, Value>) -> Result<(), Fault> {
    if fields.is_empty() || fields.len() > 20 {
        return Err(Fault::unknown("Data sets need 1–20 fields"));
    }
    for (name, field) in fields {
        text(&json!(name))?;
        shape(
            field,
            &[
                "description",
                "type",
                "required",
                "example",
                "minimum",
                "maximum",
                "minimum length",
                "maximum length",
                "trim spaces",
                "options",
            ],
            &["description", "type", "example"],
        )?;
        text(&field["description"])?;
        let kind = string(&field["type"]);
        if !["text", "email", "whole number", "number", "choice"].contains(&kind) {
            return Err(Fault::unknown("Unsupported business input type"));
        }
        for flag in ["required", "trim spaces"] {
            if field.get(flag).is_some_and(|v| !v.is_boolean()) {
                return Err(Fault::unknown("Field flags must be boolean"));
            }
        }
        if !literal(&field["example"]) {
            return Err(Fault::unknown("Examples must be literal text or numbers"));
        }
        if ["number", "whole number"].contains(&kind) {
            let min = field["minimum"].as_f64();
            let max = field["maximum"].as_f64();
            if !min.zip(max).is_some_and(|(a, b)| {
                a.is_finite()
                    && b.is_finite()
                    && a <= b
                    && a.abs() <= 1_000_000.0
                    && b.abs() <= 1_000_000.0
            }) || (kind == "whole number"
                && (!field["minimum"].is_i64() || !field["maximum"].is_i64()))
            {
                return Err(Fault::unknown(
                    "Numeric fields need ordered, bounded business limits",
                ));
            }
        } else if field.get("minimum").is_some() || field.get("maximum").is_some() {
            return Err(Fault::unknown(
                "Numeric limits only apply to numeric fields",
            ));
        }
        for key in ["minimum length", "maximum length"] {
            if let Some(v) = field.get(key)
                && (!["text", "email"].contains(&kind) || !v.as_u64().is_some_and(|n| n <= 1000))
            {
                return Err(Fault::unknown("Text lengths must be integers from 0–1000"));
            }
        }
        if field["minimum length"].as_u64().unwrap_or(0)
            > field["maximum length"].as_u64().unwrap_or(1000)
        {
            return Err(Fault::unknown("Inverted text limits"));
        }
        if kind == "choice" {
            let options = field["options"]
                .as_array()
                .filter(|a| !a.is_empty() && a.len() <= 50)
                .ok_or_else(|| Fault::unknown("Choices need 1–50 distinct options"))?;
            let mut seen = HashSet::new();
            for option in options {
                text(option)?;
                if !seen.insert(string(option)) {
                    return Err(Fault::unknown("Duplicate choice"));
                }
            }
        } else if field.get("options").is_some() {
            return Err(Fault::unknown("Options only apply to choices"));
        }
    }
    if !violations(fields, &baseline(fields)).is_empty() {
        return Err(Fault::unknown(
            "Field examples must satisfy their independent rules",
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> Value {
        serde_json::from_str(include_str!("../tests/fixtures/capacity.json")).unwrap()
    }

    #[test]
    fn rpc_metadata_survives_normalization_and_changes_replay_identity() {
        let original = Model::new(fixture()).unwrap();
        let mut document = original.document.clone();
        document["models"][0]["properties"]["rpc"] = json!({
            "version": 1,
            "fixtures": {"name":"雪", "small":0.000001},
            "adapter_owned": {"opaque":[true, null, "value"]}
        });
        document["models"][0]["vertices"][0]["properties"]["rpc"] =
            json!({"match":[{"path":"/response/result", "equals":"ready"}]});
        document["models"][0]["edges"][0]["properties"]["rpc"] =
            json!({"request":{"method":"core.info"}});
        let bound = Model::new(document.clone()).unwrap();
        assert_eq!(bound.document, document);
        assert_ne!(bound.digest, original.digest);
        assert_eq!(
            Model::new(bound.document.clone()).unwrap().digest,
            bound.digest
        );
        let state_id = string(&document["models"][0]["vertices"][0]["id"]);
        assert_eq!(
            bound.states[state_id]["properties"]["rpc"],
            document["models"][0]["vertices"][0]["properties"]["rpc"]
        );
        let edge_id = string(&document["models"][0]["edges"][0]["id"]);
        assert_eq!(
            bound.edges[edge_id]["properties"]["rpc"],
            document["models"][0]["edges"][0]["properties"]["rpc"]
        );
        document["models"][0]["properties"]["rpc"]["fixtures"]["name"] = json!("changed");
        assert_ne!(Model::new(document).unwrap().digest, bound.digest);
    }

    #[test]
    fn rpc_profiles_validate_the_boundary_without_weakening_business_fields() {
        for metadata in [
            json!(null),
            json!([]),
            json!({"version":true}),
            json!({"version":2}),
        ] {
            let mut document = fixture();
            document["models"][0]["properties"]["rpc"] = metadata;
            assert!(Model::new(document).is_err());
        }
        for kind in ["vertices", "edges"] {
            let mut document = fixture();
            document["models"][0][kind][0]["properties"]["rpc"] = json!({});
            assert!(Model::new(document.clone()).is_err());
            document["models"][0]["properties"]["rpc"] = json!({"version":1});
            assert!(Model::new(document.clone()).is_ok());
            document["models"][0][kind][0]["properties"]["rpc"] = json!([]);
            assert!(Model::new(document.clone()).is_err());
            document["models"][0][kind][0]["properties"]["rpc"] = json!({});
            document["models"][0][kind][0]["properties"]["business"]["selector"] = json!("#target");
            assert!(Model::new(document).is_err());
        }
    }
}
