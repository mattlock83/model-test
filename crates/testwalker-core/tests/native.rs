use serde_json::{Value, json};
use std::{
    io::Cursor,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
};
use testwalker_core::{
    Fault, Target,
    decision::{DecisionProvider, Decisions},
    engine::{self, Options},
    graph,
    model::{Model, violations},
    properties,
    protocol::Peer,
};

fn model() -> Model {
    Model::new(serde_json::from_str(include_str!("fixtures/capacity.json")).unwrap()).unwrap()
}
#[derive(Default)]
struct Fixture {
    state: String,
    bug: bool,
    uncertain: bool,
    calls: Vec<(String, Value)>,
    snapshots: bool,
}
impl Target for Fixture {
    fn call(&mut self, method: &str, params: Value) -> Result<Value, Fault> {
        self.calls.push((method.to_owned(), params.clone()));
        match method {
            "target.initialize" => {
                Ok(json!({"capabilities":{"isolated_checkpoints":self.snapshots}}))
            }
            "target.reset" => {
                self.state = "form".into();
                Ok(json!({}))
            }
            "target.execute" => {
                self.state = if params["fields"].as_object().unwrap().is_empty() {
                    "form"
                } else if violations(params["fields"].as_object().unwrap(), &params["input"])
                    .is_empty()
                    || (self.bug && params["input"]["Places"] == 5)
                {
                    "accepted"
                } else {
                    "rejected"
                }
                .into();
                Ok(json!({}))
            }
            "target.observe" => {
                Ok(json!({"state":self.state,"payload":{"reservation":"synthetic"}}))
            }
            "target.evaluate" => Ok(
                json!({"observed":self.state,"checks":[{"rule":"The outcome is visible.","scope":"state","status":if self.uncertain {"uncertain"}else{"met"}},{"rule":"Invalid capacity is rejected.","scope":"global","status":"met"}]}),
            ),
            "target.checkpoint" => Ok(json!({"state":self.state})),
            "target.restore" => {
                self.state = params["checkpoint"]["state"].as_str().unwrap().into();
                Ok(json!({}))
            }
            "decision.ask" => {
                let mut answers = json!({});
                for (name, question) in params["questions"].as_object().unwrap() {
                    let choice = if name == "state" {
                        params["evidence"]["state"].as_str().unwrap()
                    } else {
                        "met"
                    };
                    let probs = question["criteria"]
                        .as_object()
                        .unwrap()
                        .keys()
                        .map(|k| (k.clone(), json!(if k == choice { 1.0 } else { 0.0 })))
                        .collect::<serde_json::Map<_, _>>();
                    answers[name] = json!({"choice":choice,"confidence":1.0,"probabilities":probs});
                }
                Ok(json!({"answers":answers}))
            }
            "decision.answer" => {
                Ok(params["result"]["answers"][params["name"].as_str().unwrap()]["choice"].clone())
            }
            "target.hook" | "target.close" | "run.event" => Ok(json!({})),
            _ => Err(Fault::unknown(format!("Unexpected method {method}"))),
        }
    }
}
fn options() -> Options {
    Options {
        evaluator: "adapter".into(),
        max_steps: 1000,
        ..Default::default()
    }
}

#[test]
fn native_generators_produce_connected_walks() {
    for expression in [
        "random(edge_coverage(100))",
        "weighted_random(edge_coverage(100))",
        "quick_random(edge_coverage(100))",
        "a_star(reached_vertex(accepted))",
        "new_york_street_sweeper()",
        "shortest_all_paths(edge_coverage(100))",
        "predefined_path(predefined_path)",
    ] {
        let mut document = model().document;
        document["models"][0]["generator"] = json!(expression);
        if expression == "predefined_path(predefined_path)" {
            document["models"][0]["predefinedPathEdgeIds"] = json!(["submit", "another"]);
        }
        let model = Model::new(document).unwrap();
        let path =
            graph::path(&model, 42, 1000).unwrap_or_else(|error| panic!("{expression}: {error:?}"));
        assert!(!path.is_empty(), "{expression}");
        assert_eq!(path[0]["id"], "form");
    }
}
#[test]
fn native_coverage_and_hooks_work_without_a_browser() {
    let mut target = Fixture::default();
    let report = engine::run(&model(), &mut target, options(), json!({}), None).unwrap();
    assert_eq!(report["status"], "PASS");
    assert_eq!(report["runtime"]["properties"], "hegel");
    assert_eq!(report["coverage"]["edges"]["verified"], 4);
    assert!(
        target
            .calls
            .iter()
            .any(|(m, p)| m == "target.hook" && p["event"] == "after_run")
    );
    assert_eq!(target.calls.last().unwrap().0, "target.close");
}
#[test]
fn uncertainty_stops_without_shrinking_into_a_defect() {
    let mut target = Fixture {
        uncertain: true,
        ..Default::default()
    };
    let report = engine::run(&model(), &mut target, options(), json!({}), None).unwrap();
    assert_eq!(report["status"], "INCONCLUSIVE");
    assert_eq!(report["input_attempts"], 0);
    assert!(
        report["tests"]
            .as_array()
            .unwrap()
            .iter()
            .any(|t| t["status"] == "SKIPPED")
    );
    assert_eq!(target.calls.last().unwrap().0, "target.close");
}
#[test]
fn selected_input_scope_is_a_pass_and_replays_independently() {
    let mut opts = options();
    opts.max_input_attempts = 2;
    let mut target = Fixture::default();
    let report = engine::run(&model(), &mut target, opts.clone(), json!({}), None).unwrap();
    assert_eq!(report["status"], "PASS");
    assert_eq!(report["input_attempts"], 2);
    let recipe = report["cases"][0]["replay_recipe"].clone();
    let mut fresh = Fixture::default();
    let replay = engine::run(&model(), &mut fresh, opts, json!({}), Some(recipe)).unwrap();
    assert_eq!(replay["status"], "PASS");
    assert_eq!(replay["input_attempts"], 1);
}
#[test]
fn isolated_checkpoint_avoids_resets_without_changing_case_inputs() {
    let mut target = Fixture {
        snapshots: true,
        ..Default::default()
    };
    let report = engine::run(&model(), &mut target, options(), json!({}), None).unwrap();
    assert_eq!(report["status"], "PASS");
    assert_eq!(
        target
            .calls
            .iter()
            .filter(|(m, _)| m == "target.checkpoint")
            .count(),
        1
    );
    assert_eq!(
        target
            .calls
            .iter()
            .filter(|(m, _)| m == "target.reset")
            .count(),
        2
    ); // graph and campaign setup
    assert!(target.calls.iter().any(|(m, _)| m == "target.restore"));
}
#[test]
fn native_hegel_shrinks_to_smallest_reproducible_violation() {
    let model = model();
    let fields = model.data_sets["Reservation"].as_object().unwrap();
    let phase = json!({"kind":"generated","field":"Places","max_examples":50,"domain":{"kind":"integer","minimum":0,"maximum":100}});
    let mut inputs = Vec::new();
    let outcome = properties::exercise(fields, &phase, 42, true, |data| {
        inputs.push(data.clone());
        if data["Places"].as_i64().unwrap() >= 5 {
            Err(Fault::defect(
                "Capacity bug",
                json!({"case":{"input":data}}),
            ))
        } else {
            Ok(json!({"status":"PASS"}))
        }
    })
    .unwrap_err();
    assert_eq!(outcome.status, "FAIL");
    assert_eq!(outcome.result["case"]["input"]["Places"], 5);
    assert!(inputs.iter().any(|d| d["Places"].as_i64().unwrap() < 5));
}
#[test]
fn successful_singletons_are_not_resubmitted() {
    let model = model();
    let fields = model.data_sets["Reservation"].as_object().unwrap();
    let mut calls = 0;
    properties::exercise(fields,&json!({"kind":"generated","field":"Places","max_examples":100,"domain":{"kind":"literal","value":2}}),42,true,|_|{calls+=1;Ok(json!({}))}).unwrap();
    assert_eq!(calls, 1);
}
#[test]
fn provider_fault_is_latched_at_first_property_attempt() {
    let model = model();
    let fields = model.data_sets["Reservation"].as_object().unwrap();
    let mut calls = 0;
    let err = properties::exercise(
        fields,
        &json!({"kind":"generated","field":"Places","max_examples":20}),
        42,
        true,
        |_| {
            calls += 1;
            Err(Fault::unknown("Service offline"))
        },
    )
    .unwrap_err();
    assert_eq!(calls, 1);
    assert_eq!(err.status, "INCONCLUSIVE");
    assert_eq!(err.message, "Service offline");
}
#[test]
fn malformed_custom_domains_are_rejected_before_generation() {
    for domain in [
        json!({"kind":"integer","minimum":5,"maximum":1}),
        json!({"kind":"choice","values":[]}),
        json!({"kind":"text","alphabet":"","minimum length":0,"maximum length":1}),
        json!({"kind":"literal","value":null}),
        json!({"kind":"default","selector":"#input"}),
    ] {
        assert!(properties::validate_domain(&domain, 0).is_err());
    }
}
struct Provider {
    calls: Arc<AtomicUsize>,
    fail: bool,
}
impl DecisionProvider for Provider {
    fn ask(&mut self, _: &Value, _: &Value) -> Result<Value, Fault> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        if self.fail {
            Err(Fault::unknown("Provider unavailable"))
        } else {
            Ok(json!({"answers":{},"usage":{"input_tokens":10,"output_tokens":3}}))
        }
    }
}
#[test]
fn provider_budget_cache_and_usage_are_native() {
    let calls = Arc::new(AtomicUsize::new(0));
    let mut decisions = Decisions::new(
        Box::new(Provider {
            calls: calls.clone(),
            fail: false,
        }),
        1,
        0.8,
    )
    .unwrap();
    assert_eq!(
        decisions.ask(&json!({"status":200}), &json!({})).unwrap(),
        decisions.ask(&json!({"status":200}), &json!({})).unwrap()
    );
    assert!(decisions.ask(&json!({"status":201}), &json!({})).is_err());
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert_eq!(decisions.cache_hits, 1);
    assert_eq!(decisions.input_tokens, 10);
}
#[test]
fn confidence_and_choice_validation_cannot_be_bypassed() {
    let decisions = Decisions::new(
        Box::new(Provider {
            calls: Arc::new(AtomicUsize::new(0)),
            fail: false,
        }),
        10,
        0.85,
    )
    .unwrap();
    let result = json!({"answers":{"state":{"choice":"a","confidence":0.8,"probabilities":{"a":0.8,"b":0.2}}}});
    assert!(
        decisions
            .answer(&result, "state", &json!(["a", "b"]), None)
            .is_err()
    );
    assert_eq!(
        decisions
            .answer(&result, "state", &json!(["a", "b"]), Some(0.75))
            .unwrap(),
        "a"
    );
    assert!(decisions.answer(&json!({"answers":{"state":{"choice":"a","confidence":1,"probabilities":{"a":0.1,"b":0.9}}}}),"state",&json!(["a","b"]),None).is_err());
}
#[test]
fn json_rpc_answers_requests_and_recovers_from_invalid_json() {
    let input=b"invalid\n{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"core.info\"}\n{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"missing\"}\n";
    let mut output = Vec::new();
    Peer::new(Cursor::new(input), &mut output).serve().unwrap();
    let values = String::from_utf8(output)
        .unwrap()
        .lines()
        .map(|s| serde_json::from_str::<Value>(s).unwrap())
        .collect::<Vec<_>>();
    assert_eq!(values[0]["error"]["code"], -32700);
    assert_eq!(values[1]["result"]["protocol_version"], "1");
    assert_eq!(values[2]["error"]["code"], -32601);
}

#[test]
fn core_evaluator_asserts_on_json_observations_without_web_bindings() {
    let mut target = Fixture::default();
    let report = engine::run(
        &model(),
        &mut target,
        Options {
            max_steps: 1000,
            max_input_attempts: 2,
            ..Default::default()
        },
        json!({"transport":"api"}),
        None,
    )
    .unwrap();
    assert_eq!(report["status"], "PASS");
    assert!(!target.calls.iter().any(|(m, _)| m == "target.evaluate"));
    assert!(target.calls.iter().any(|(m, _)| m == "decision.ask"));
}
#[test]
fn malformed_models_fail_without_panicking() {
    for document in [json!("bad"), json!([]), json!(null), json!({"models":[{}]})] {
        assert!(Model::new(document).is_err());
    }
    let mut document = model().document;
    document["models"][0]["actions"] = json!("arbitrary code");
    assert!(Model::new(document).is_err());
}

#[test]
fn report_enrichment_cannot_rewrite_cases_or_verdicts() {
    struct Poisoned(Fixture);
    impl Target for Poisoned {
        fn call(&mut self, method: &str, params: Value) -> Result<Value, Fault> {
            if method == "run.event"
                && ["case", "test.end"].contains(&params["type"].as_str().unwrap_or(""))
            {
                return Ok(
                    json!({"status":"FAIL","input":{"Places":999},"id":"wrong","attachments":{"note":"custom evidence"}}),
                );
            }
            self.0.call(method, params)
        }
    }
    let mut target = Poisoned(Fixture::default());
    let report = engine::run(
        &model(),
        &mut target,
        Options {
            max_input_attempts: 1,
            ..options()
        },
        json!({}),
        None,
    )
    .unwrap();
    assert_eq!(report["status"], "PASS");
    assert_eq!(report["cases"][0]["input"]["Places"], 2);
    assert_eq!(report["cases"][0]["status"], "PASS");
    assert_eq!(report["cases"][0]["attachments"]["note"], "custom evidence");
    assert!(
        !report["tests"]
            .as_array()
            .unwrap()
            .iter()
            .any(|test| test["id"] == "wrong")
    );
}
