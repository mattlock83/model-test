//! Wire-level assertions independent of the execution model and target adapter.
use serde_json::{Value, json};
use std::io::Cursor;
use testwalker_core::protocol::Peer;

fn exchange(lines: &[Value]) -> Vec<Value> {
    let input = lines
        .iter()
        .map(Value::to_string)
        .collect::<Vec<_>>()
        .join("\n")
        + "\n";
    exchange_raw(&input)
}

fn exchange_raw(input: &str) -> Vec<Value> {
    let mut output = Vec::new();
    Peer::new(Cursor::new(input.as_bytes()), &mut output)
        .serve()
        .unwrap();
    String::from_utf8(output)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}

fn info(id: Value) -> Value {
    json!({"jsonrpc":"2.0","id":id,"method":"core.info"})
}

fn assert_error(response: &Value, id: Value, code: i64) {
    assert_eq!(response["jsonrpc"], "2.0");
    assert_eq!(response["id"], id);
    assert_eq!(response["error"]["code"], code);
    assert!(response["error"]["message"].is_string());
    assert!(response.get("result").is_none());
}

#[test]
fn response_identifiers_preserve_json_type_and_value() {
    let ids = [json!("request/1"), json!(17), json!(0), Value::Null];
    let responses = exchange(&ids.iter().cloned().map(info).collect::<Vec<_>>());
    assert_eq!(responses.len(), ids.len());
    for (response, id) in responses.iter().zip(ids) {
        assert_eq!(response["jsonrpc"], "2.0");
        assert_eq!(response["id"], id);
        assert_eq!(response["result"]["name"], "testwalker-core");
        assert!(response.get("error").is_none());
    }
}

#[test]
fn valid_notifications_do_not_receive_success_or_error_responses() {
    let responses = exchange(&[
        json!({"jsonrpc":"2.0","method":"core.info"}),
        json!({"jsonrpc":"2.0","method":"unknown.method"}),
        json!({"jsonrpc":"2.0","method":"model.validate","params":{"model":null}}),
        json!({"jsonrpc":"2.0","method":"core.shutdown","params":42}),
        info(json!("still-running")),
    ]);
    assert_eq!(responses.len(), 1);
    assert_eq!(responses[0]["id"], "still-running");
    assert_eq!(responses[0]["result"]["name"], "testwalker-core");
}

#[test]
fn invalid_envelopes_respond_with_invalid_request_and_recover() {
    for request in [
        json!({}),
        json!({"jsonrpc":"1.0","method":"core.info"}),
        json!({"jsonrpc":"2.0"}),
        json!({"jsonrpc":"2.0","method":17}),
        json!(null),
        json!(true),
        json!(17),
        json!("core.info"),
        json!([]),
        json!([{"jsonrpc":"2.0","id":1,"method":"core.info"}]),
    ] {
        let responses = exchange(&[request, info(json!("recovered"))]);
        assert_eq!(responses.len(), 2);
        assert_error(&responses[0], Value::Null, -32600);
        assert_eq!(responses[1]["id"], "recovered");
        assert_eq!(responses[1]["result"]["name"], "testwalker-core");
    }
}

#[test]
fn invalid_request_preserves_only_a_usable_identifier() {
    for id in [json!(true), json!([]), json!({"id":1})] {
        let responses = exchange(&[info(id)]);
        assert_eq!(responses.len(), 1);
        assert_error(&responses[0], Value::Null, -32600);
    }
    for id in [json!("request/1"), json!(17), Value::Null] {
        let responses = exchange(&[
            json!({"jsonrpc":"1.0","id":id,"method":"core.shutdown"}),
            info(json!("still-running")),
        ]);
        assert_eq!(responses.len(), 2);
        assert_error(&responses[0], id, -32600);
        assert_eq!(responses[1]["id"], "still-running");
    }
}

#[test]
fn scalar_parameters_are_rejected_without_executing_the_method() {
    for params in [json!(null), json!(true), json!(17), json!("invalid")] {
        let responses = exchange(&[
            json!({"jsonrpc":"2.0","id":"invalid","method":"core.shutdown","params":params}),
            info(json!("still-running")),
        ]);
        assert_eq!(responses.len(), 2);
        assert_error(&responses[0], json!("invalid"), -32602);
        assert_eq!(responses[1]["result"]["name"], "testwalker-core");
    }
}

#[test]
fn parse_method_and_domain_errors_remain_distinguishable() {
    let responses = exchange_raw(
        "invalid json\n\
         {\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"unknown.method\"}\n\
         {\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"model.validate\",\"params\":{\"model\":null}}\n\
         {\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"core.info\"}\n",
    );
    assert_eq!(responses.len(), 4);
    assert_error(&responses[0], Value::Null, -32700);
    assert_error(&responses[1], json!(1), -32601);
    assert_error(&responses[2], json!(2), -32001);
    assert_eq!(responses[2]["error"]["data"]["status"], "INCONCLUSIVE");
    assert_eq!(responses[3]["result"]["name"], "testwalker-core");
}

#[test]
fn graceful_shutdown_acknowledges_then_stops_reading_requests() {
    let responses = exchange(&[
        json!({"jsonrpc":"2.0","id":1,"method":"core.shutdown"}),
        info(json!(2)),
    ]);
    assert_eq!(responses, vec![json!({"jsonrpc":"2.0","id":1,"result":{}})]);
}
