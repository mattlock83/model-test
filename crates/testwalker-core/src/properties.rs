//! Native Hegel strategies, partition planning and failure minimization.
use crate::{
    Fault, Target,
    model::{baseline, decimal, rendered, string, violations},
};
use hegel::{
    HealthCheck, Hegel, NondeterminismStrictness, Phase, Settings, TestCase, Verbosity,
    generators as gs,
};
use rust_decimal::Decimal;
use serde_json::{Map, Value, json};
use std::{
    panic::{AssertUnwindSafe, catch_unwind, panic_any},
    sync::Arc,
};

fn numeric(n: Decimal) -> Value {
    if n.fract().is_zero() {
        json!(n.to_string().parse::<i64>().unwrap())
    } else {
        json!(n.to_string().parse::<f64>().unwrap())
    }
}
pub fn boundary_samples(field: &Value) -> Vec<(String, Value)> {
    let mut samples = vec![
        ("valid example".into(), field["example"].clone()),
        ("blank".into(), json!("")),
    ];
    if field["required"] == true {
        samples.push(("whitespace only".into(), json!("   ")));
    }
    match string(&field["type"]) {
        "whole number" | "number" => {
            for name in ["minimum", "maximum"] {
                let limit = decimal(&field[name]).unwrap();
                for offset in [-1, 0, 1] {
                    samples.push((
                        format!("{name} {offset:+}"),
                        numeric(limit + Decimal::from(offset)),
                    ));
                }
            }
            if field["type"] == "whole number" {
                samples.push((
                    "fraction instead of whole number".into(),
                    json!((decimal(&field["minimum"]).unwrap() + Decimal::new(5, 1)).to_string()),
                ));
            }
            samples.push(("non-numeric text".into(), json!("not a number")));
        }
        "text" | "email" => {
            for name in ["minimum length", "maximum length"] {
                if let Some(limit) = field[name].as_i64() {
                    for offset in [-1, 0, 1] {
                        let size = limit + offset;
                        if size < 0 {
                            continue;
                        }
                        let value = if field["type"] == "email" && size >= 5 {
                            format!("{}@e.t", "x".repeat(size as usize - 4))
                        } else {
                            "x".repeat(size as usize)
                        };
                        samples.push((format!("{name} {offset:+}"), json!(value)));
                    }
                }
            }
            samples.push((
                "surrounding whitespace".into(),
                json!(format!("  {}  ", rendered(&field["example"]))),
            ));
            if field["type"] == "email" {
                for (name, value) in [
                    ("email missing @", "missing-at"),
                    ("email missing domain suffix", "a@localhost"),
                    ("email contains whitespace", "a @example.test"),
                ] {
                    samples.push((name.into(), json!(value)));
                }
            }
        }
        _ => {
            for option in field["options"].as_array().unwrap() {
                samples.push(("permitted choice".into(), option.clone()));
            }
            let mut unknown = "unlisted choice".to_owned();
            while field["options"]
                .as_array()
                .unwrap()
                .contains(&json!(unknown))
            {
                unknown.push('!');
            }
            samples.push(("unlisted choice".into(), json!(unknown)));
        }
    }
    let mut unique = Vec::new();
    for sample in samples {
        if !unique
            .iter()
            .any(|(_, v): &(String, Value)| equal(v, &sample.1))
        {
            unique.push(sample);
        }
    }
    unique
}
fn equal(a: &Value, b: &Value) -> bool {
    a == b || (a.is_number() && b.is_number() && decimal(a) == decimal(b))
}
fn options(policy: &Value, data_set: &str, name: &str, cases: u64) -> Value {
    let mut options = json!({"strategy":"nearby","cases":cases,"radius":1,"alphabet":"abcdefghijklmnopqrstuvwxyz"});
    for source in [&policy["defaults"], &policy["data sets"][data_set][name]] {
        if let Some(map) = source.as_object() {
            for (k, v) in map {
                options[k] = v.clone();
            }
        }
    }
    options
}
pub fn plan_cases(
    fields: &Map<String, Value>,
    cases: u64,
    mode: &str,
    policy: &Value,
    data_set: &str,
) -> Vec<Value> {
    let baseline = baseline(fields);
    let mut planned = Vec::new();
    if ["all", "generated"].contains(&mode) {
        for (index, name) in fields.keys().enumerate() {
            planned.push(json!({"id":format!("generated/{index}"),"kind":"generated","field":name,"source":format!("generated: {name}"),"max_examples":cases}));
        }
        planned.push(json!({"id":"generated/combined","kind":"generated","source":"generated: combined","max_examples":cases}));
    }
    if ["all", "boundaries", "focused"].contains(&mode) {
        let mut seen: Vec<Value> = Vec::new();
        for (index, (name, field)) in fields.iter().enumerate() {
            for (sample, (reason, value)) in boundary_samples(field).into_iter().enumerate() {
                let mut data = baseline.clone();
                data[name] = value;
                if seen
                    .iter()
                    .any(|d| fields.keys().all(|k| equal(&d[k], &data[k])))
                {
                    continue;
                }
                seen.push(data.clone());
                let mut phase = json!({"id":format!("boundary/{index}/{sample}"),"kind":"boundary","field":name,"source":format!("boundary: {name} — {reason}"),"input":data,"violations":violations(fields,&data)});
                if mode == "focused" {
                    let strategy = options(policy, data_set, name, cases);
                    phase["id"] = json!(format!("focused/{index}/{sample}"));
                    phase["kind"] = json!("generated");
                    phase["source"] = json!(format!("focused: {name} — {reason}"));
                    phase["partition"] = json!(reason);
                    phase["strategy"] = strategy.clone();
                    phase["data_set"] = json!(data_set);
                    phase["max_examples"] = strategy["cases"].clone();
                    phase["boundary_input"] =
                        phase.as_object_mut().unwrap().remove("input").unwrap();
                }
                planned.push(phase);
            }
        }
    }
    planned
}
fn text(tc: &TestCase, alphabet: &str, min: usize, max: usize) -> String {
    tc.draw_silent(gs::text().alphabet(alphabet).min_size(min).max_size(max))
}
fn focused(
    tc: &TestCase,
    field: &Value,
    partition: &str,
    reference: &Value,
    options: &Value,
) -> Value {
    if options["strategy"] == "boundary" {
        return tc.draw_silent(gs::sampled_from(vec![reference.clone()]));
    }
    let radius = options["radius"].as_i64().unwrap_or(1);
    let alphabet = options["alphabet"]
        .as_str()
        .unwrap_or("abcdefghijklmnopqrstuvwxyz");
    match partition {
        "minimum -1" | "maximum +1" => {
            let direction = if partition == "minimum -1" { -1 } else { 1 };
            let limit = decimal(&field[if direction < 0 { "minimum" } else { "maximum" }]).unwrap();
            numeric(
                limit
                    + Decimal::from(
                        direction
                            * tc.draw_silent(gs::integers::<i64>().min_value(1).max_value(radius)),
                    ),
            )
        }
        p if p.starts_with("minimum length") || p.starts_with("maximum length") => {
            let len = string(reference).chars().count();
            if field["type"] == "email" && len >= 5 {
                json!(format!("{}@e.t", text(tc, alphabet, len - 4, len - 4)))
            } else {
                json!(text(tc, alphabet, len, len))
            }
        }
        "whitespace only" => json!(text(tc, " ", 1, (3 * radius as usize).min(2000))),
        "non-numeric text" | "email missing @" | "unlisted choice" => {
            json!(text(tc, alphabet, 1, (10 * radius as usize).min(1900)))
        }
        "email missing domain suffix" => json!(format!(
            "{}@localhost",
            text(
                tc,
                "abcdefghijklmnopqrstuvwxyz",
                1,
                (10 * radius as usize).min(1900)
            )
        )),
        "email contains whitespace" => json!(format!(
            "{} @example.test",
            text(
                tc,
                "abcdefghijklmnopqrstuvwxyz",
                1,
                (10 * radius as usize).min(1900)
            )
        )),
        "fraction instead of whole number" => json!(
            (decimal(&field["minimum"]).unwrap()
                + Decimal::new(
                    tc.draw_silent(gs::integers::<i64>().min_value(1).max_value(9)),
                    1
                ))
            .to_string()
        ),
        _ => tc.draw_silent(gs::sampled_from(vec![reference.clone()])),
    }
}
fn broad(tc: &TestCase, field: &Value) -> Value {
    // Hegel chooses the strategy and every generated value, including shrinking.
    if tc.draw_silent(gs::integers::<u8>().max_value(1)) == 0 {
        return tc.draw_silent(gs::sampled_from(
            boundary_samples(field)
                .into_iter()
                .map(|(_, v)| v)
                .collect::<Vec<_>>(),
        ));
    }
    match string(&field["type"]) {
        "whole number" => json!(
            tc.draw_silent(
                gs::integers::<i64>()
                    .min_value(field["minimum"].as_i64().unwrap() - 1)
                    .max_value(field["maximum"].as_i64().unwrap() + 1)
            )
        ),
        "number" => json!(
            tc.draw_silent(
                gs::floats::<f64>()
                    .min_value(field["minimum"].as_f64().unwrap() - 1.0)
                    .max_value(field["maximum"].as_f64().unwrap() + 1.0)
                    .allow_nan(false)
                    .allow_infinity(false)
            )
        ),
        "text" => json!(text(
            tc,
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._-",
            0,
            field["maximum length"].as_u64().unwrap_or(100) as usize + 1
        )),
        _ => tc.draw_silent(gs::sampled_from(
            boundary_samples(field)
                .into_iter()
                .map(|(_, v)| v)
                .collect::<Vec<_>>(),
        )),
    }
}
pub fn draw(tc: &TestCase, fields: &Map<String, Value>, phase: &Value) -> Value {
    if phase["kind"] == "boundary" {
        return tc.draw_silent(gs::sampled_from(vec![phase["input"].clone()]));
    }
    let mut data = baseline(fields);
    if let Some(name) = phase["field"].as_str() {
        if let Some(partition) = phase["partition"].as_str() {
            data[name] = if phase["domain"].is_object() && phase["domain"]["kind"] != "default" {
                domain(tc, &phase["domain"])
            } else {
                focused(
                    tc,
                    &fields[name],
                    partition,
                    &phase["boundary_input"][name],
                    &phase["strategy"],
                )
            };
            tc.assume(json!(violations(fields, &data)) == phase["violations"]);
        } else {
            data[name] = if phase["domain"].is_object() && phase["domain"]["kind"] != "default" {
                domain(tc, &phase["domain"])
            } else {
                broad(tc, &fields[name])
            };
        }
    } else if phase["domain"].is_object() && phase["domain"]["kind"] != "default" {
        data = domain(tc, &phase["domain"]);
    } else {
        for (name, field) in fields {
            data[name] = broad(tc, field);
        }
    }
    data
}
#[derive(Debug)]
struct CaseFailure(Fault);

pub fn exercise<F>(
    fields: &Map<String, Value>,
    phase: &Value,
    seed: u64,
    shrink: bool,
    mut execute: F,
) -> Result<(), Fault>
where
    F: FnMut(Value) -> Result<Value, Fault>,
{
    let mut infrastructure: Option<Fault> = None;
    let mut last_failure: Option<Fault> = None;
    let mut passed = std::collections::HashSet::new();
    let settings = Settings::new()
        .test_cases(phase["max_examples"].as_u64().unwrap_or(1))
        .seed(Some(seed))
        .database(None)
        .verbosity(Verbosity::Quiet)
        .phases(if shrink {
            vec![Phase::Generate, Phase::Shrink]
        } else {
            vec![Phase::Generate]
        })
        .nondeterminism_strictness(NondeterminismStrictness::Error)
        .suppress_health_check([HealthCheck::TooSlow])
        .report_multiple_failures(false);
    let result = catch_unwind(AssertUnwindSafe(|| {
        hegel::with_output_override(Arc::new(|line| eprintln!("{line}")), || {
            Hegel::new(|tc| {
                if let Some(error) = &infrastructure {
                    panic_any(CaseFailure(error.clone()));
                }
                let data = draw(&tc, fields, phase);
                let encoded = data.to_string();
                if passed.contains(&encoded) {
                    return;
                }
                if let Err(error) = execute(data) {
                    if error.status == "FAIL" {
                        last_failure = Some(error.clone());
                    } else {
                        infrastructure = Some(error.clone());
                    }
                    panic_any(CaseFailure(error));
                }
                passed.insert(encoded);
            })
            .settings(settings)
            .run();
        });
    }));
    if let Some(error) = infrastructure {
        if error.result["input_limit"] == true
            && let Some(mut failure) = last_failure
        {
            failure.result["case"]["minimization_limited"] = json!(true);
            failure.result["case"]["minimization_note"] = json!(
                "The input limit stopped reproduction or shrinking; the recorded failing input is retained without a minimality guarantee."
            );
            return Err(failure);
        }
        return Err(error);
    }
    match result {
        Ok(()) => Ok(()),
        Err(payload) => match payload.downcast::<CaseFailure>() {
            Ok(failure) => Err(failure.0),
            Err(payload) => {
                let message = payload
                    .downcast_ref::<String>()
                    .map(String::as_str)
                    .or_else(|| payload.downcast_ref::<&str>().copied())
                    .unwrap_or("Unknown Hegel failure");
                Err(Fault::unknown(format!(
                    "Hegel could not establish a reproducible property outcome: {message}"
                )))
            }
        },
    }
}

/// Bind a trusted language extension to a native, shrinkable Hegel domain.
pub fn bind_strategy<T: Target>(
    fields: &Map<String, Value>,
    phase: &mut Value,
    target: &mut T,
) -> Result<(), Fault> {
    if phase["kind"] != "generated" {
        return Ok(());
    }
    let name = phase["field"].as_str();
    let context = json!({"data_set":phase["data_set"],"field_name":name,"field":name.map(|n|&fields[n]),"fields":fields,"partition":phase["partition"],"reference":name.map(|n|&phase["boundary_input"][n]),"options":phase["strategy"],"violations":phase["violations"]});
    let descriptor = target.call(
        "target.strategy",
        json!({"context":context,"default":{"kind":"default"}}),
    )?;
    validate_domain(&descriptor, 0)?;
    if name.is_none() && descriptor["kind"] != "default" {
        if descriptor["kind"] != "object"
            || descriptor["fields"].as_object().unwrap().len() != fields.len()
            || !fields.keys().all(|k| descriptor["fields"].get(k).is_some())
        {
            return Err(Fault::unknown(
                "Combined strategies must describe exactly the modeled fields",
            ));
        }
    } else if descriptor["kind"] == "object" {
        return Err(Fault::unknown(
            "Field strategies must produce one literal value",
        ));
    }
    phase["domain"] = descriptor;
    Ok(())
}
pub fn validate_domain(value: &Value, depth: usize) -> Result<(), Fault> {
    let fail = || {
        Fault::unknown(
            "Invalid Hegel domain: use default, literal, choice, integer, number, text or object with bounded values",
        )
    };
    let map = value.as_object().ok_or_else(fail)?;
    if depth > 2 {
        return Err(fail());
    }
    let allowed: &[&str] = match string(&value["kind"]) {
        "default" => &["kind"],
        "literal" => {
            if !crate::model::literal(&value["value"]) {
                return Err(fail());
            }
            &["kind", "value"]
        }
        "choice" => {
            if !value["values"].as_array().is_some_and(|a| {
                !a.is_empty() && a.len() <= 200 && a.iter().all(crate::model::literal)
            }) {
                return Err(fail());
            }
            &["kind", "values"]
        }
        "integer" | "number" => {
            let min = value["minimum"].as_f64();
            let max = value["maximum"].as_f64();
            if !min.zip(max).is_some_and(|(a, b)| {
                a.is_finite()
                    && b.is_finite()
                    && a <= b
                    && a.abs() <= 10000000.0
                    && b.abs() <= 10000000.0
            }) || (value["kind"] == "integer"
                && (!value["minimum"].is_i64() || !value["maximum"].is_i64()))
            {
                return Err(fail());
            }
            &["kind", "minimum", "maximum"]
        }
        "text" => {
            if !value["alphabet"]
                .as_str()
                .is_some_and(|s| !s.is_empty() && s.chars().count() <= 1000)
                || !value["minimum length"]
                    .as_u64()
                    .zip(value["maximum length"].as_u64())
                    .is_some_and(|(a, b)| a <= b && b <= 2000)
            {
                return Err(fail());
            }
            &["kind", "alphabet", "minimum length", "maximum length"]
        }
        "object" => {
            let fields = value["fields"]
                .as_object()
                .filter(|m| !m.is_empty() && m.len() <= 20)
                .ok_or_else(fail)?;
            for item in fields.values() {
                if item["kind"] == "default" || item["kind"] == "object" {
                    return Err(fail());
                }
                validate_domain(item, depth + 1)?;
            }
            &["kind", "fields"]
        }
        _ => return Err(fail()),
    };
    if map.keys().any(|k| !allowed.contains(&k.as_str()))
        || allowed.iter().any(|k| !map.contains_key(*k))
    {
        return Err(fail());
    }
    Ok(())
}
fn domain(tc: &TestCase, value: &Value) -> Value {
    match string(&value["kind"]) {
        "literal" => tc.draw_silent(gs::sampled_from(vec![value["value"].clone()])),
        "choice" => tc.draw_silent(gs::sampled_from(
            value["values"].as_array().unwrap().clone(),
        )),
        "integer" => json!(
            tc.draw_silent(
                gs::integers::<i64>()
                    .min_value(value["minimum"].as_i64().unwrap())
                    .max_value(value["maximum"].as_i64().unwrap())
            )
        ),
        "number" => json!(
            tc.draw_silent(
                gs::floats::<f64>()
                    .min_value(value["minimum"].as_f64().unwrap())
                    .max_value(value["maximum"].as_f64().unwrap())
                    .allow_nan(false)
                    .allow_infinity(false)
            )
        ),
        "text" => json!(text(
            tc,
            string(&value["alphabet"]),
            value["minimum length"].as_u64().unwrap() as usize,
            value["maximum length"].as_u64().unwrap() as usize
        )),
        "object" => Value::Object(
            value["fields"]
                .as_object()
                .unwrap()
                .iter()
                .map(|(name, item)| (name.clone(), domain(tc, item)))
                .collect(),
        ),
        _ => unreachable!("Domains are validated before execution"),
    }
}
