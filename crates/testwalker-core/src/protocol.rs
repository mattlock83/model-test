//! Bidirectional JSON-RPC 2.0 over newline-delimited UTF-8 on stdio.
//! stdout is protocol only. Nested decision calls are handled while awaiting adapters.
use crate::{
    Fault, Target,
    decision::{Decisions, JevProvider},
    engine::{self, Options},
    graph,
    model::{Model, string, validate_fields, violations},
    properties,
};
use serde_json::{Value, json};
use std::io::{BufRead, Write};

const MAX_MESSAGE: usize = 64 * 1024 * 1024;
pub struct Peer<R: BufRead, W: Write> {
    input: R,
    output: W,
    sequence: u64,
    decisions: Option<Decisions>,
    active: bool,
    cancelled: bool,
    results: Value,
    shutdown: bool,
}
impl<R: BufRead, W: Write> Peer<R, W> {
    pub fn new(input: R, output: W) -> Self {
        Self {
            input,
            output,
            sequence: 0,
            decisions: None,
            active: false,
            cancelled: false,
            results: Value::Null,
            shutdown: false,
        }
    }
    fn read(&mut self) -> Result<Option<Value>, Fault> {
        let mut buffer = Vec::new();
        let count = std::io::Read::take(&mut self.input, MAX_MESSAGE as u64 + 1)
            .read_until(b'\n', &mut buffer)
            .map_err(|_| Fault::unknown("Cannot read the JSON-RPC connection"))?;
        if count == 0 {
            return Ok(None);
        }
        if count > MAX_MESSAGE {
            return Err(Fault::unknown("JSON-RPC message exceeds 64 MiB"));
        }
        serde_json::from_slice(&buffer)
            .map(Some)
            .map_err(|_| Fault::unknown("Invalid JSON-RPC JSON"))
    }
    fn write(&mut self, value: &Value) -> Result<(), Fault> {
        serde_json::to_writer(&mut self.output, value)
            .map_err(|_| Fault::unknown("Cannot serialize JSON-RPC message"))?;
        self.output
            .write_all(b"\n")
            .and_then(|()| self.output.flush())
            .map_err(|_| Fault::unknown("The JSON-RPC connection closed"))
    }
    fn respond(&mut self, request: &Value, result: Result<Value, Fault>) -> Result<(), Fault> {
        if let Some(id) = request.get("id") {
            let response = match result {
                Ok(value) => json!({"jsonrpc":"2.0","id":id,"result":value}),
                Err(error) => json!({"jsonrpc":"2.0","id":id,"error":error.rpc()}),
            };
            self.write(&response)?;
        }
        Ok(())
    }
    fn method(&mut self, name: &str, params: &Value) -> Result<Value, Fault> {
        match name {
            "core.info" => Ok(
                json!({"name":"testwalker-core","version":env!("CARGO_PKG_VERSION"),"protocol_version":"1","transport":"json-rpc-2.0/ndjson","graphwalker_revision":"21e6b7d72c3daab26d9cd6a1dff899275e411b23","hegel_revision":"33d94c659e441127ce7b5a0f046ec4aa9315d794","methods":["model.validate","graph.plan","property.plan","property.samples","property.exercise","run.plan","run.start","run.cancel","run.results","case.replay","decision.configure","decision.ask","decision.answer","decision.post","decision.stats","decision.audit","core.shutdown"]}),
            ),
            "core.shutdown" => {
                if self.active {
                    return Err(Fault::unknown("Cancel the active run before shutdown"));
                }
                self.shutdown = true;
                Ok(json!({}))
            }
            "model.validate" => {
                let model = Model::new(params["model"].clone())?;
                Ok(
                    json!({"model_hash":model.digest,"states":model.states.len(),"edges":model.edges.len(),"data_sets":model.data_sets.len()}),
                )
            }
            "property.plan" | "property.samples" | "property.exercise" => {
                let mut fields = params["fields"]
                    .as_object()
                    .cloned()
                    .ok_or_else(|| Fault::unknown("Supply business fields"))?;
                for (name, field) in &mut fields {
                    if !field.is_object() {
                        return Err(Fault::unknown("Business fields must be objects"));
                    }
                    if field.get("description").is_none() {
                        field["description"] = json!(name);
                    }
                }
                validate_fields(&fields)?;
                if name == "property.samples" {
                    if fields.len() != 1 {
                        return Err(Fault::unknown("Supply one field"));
                    }
                    return Ok(json!(properties::boundary_samples(
                        fields.values().next().unwrap()
                    )));
                }
                let options: Options =
                    serde_json::from_value(params.get("options").cloned().unwrap_or(json!({})))
                        .map_err(|e| Fault::unknown(format!("Invalid property options: {e}")))?;
                options.validate()?;
                let mut phases = properties::plan_cases(
                    &fields,
                    options.cases,
                    &options.input_mode,
                    &options.input_strategies,
                    params["data_set"].as_str().unwrap_or(""),
                );
                if name == "property.plan" {
                    return Ok(json!(phases));
                }
                if self.active {
                    return Err(Fault::unknown("This worker already has an active run"));
                }
                if let Some(ids) = params["phase_ids"].as_array() {
                    if ids.iter().any(|id| !phases.iter().any(|p| p["id"] == *id)) {
                        return Err(Fault::unknown("Unknown property phase"));
                    }
                    phases.retain(|p| ids.contains(&p["id"]));
                }
                for phase in &mut phases {
                    if options.custom_strategies {
                        properties::bind_strategy(&fields, phase, self)?;
                    }
                }
                self.active = true;
                self.cancelled = false;
                let outcome = (|| {
                    for phase in &phases {
                        properties::exercise(
                            &fields,
                            phase,
                            options.seed,
                            options.shrink,
                            |data| {
                                let invalid = violations(&fields, &data);
                                let result=self.call("property.evaluate",json!({"input":data,"violations":invalid,"source":phase["source"],"phase":phase}))?;
                                match result["status"].as_str() {
                                    Some("PASS") => Ok(result),
                                    Some("FAIL") => Err(Fault::defect(
                                        "The submitted business data violated a modeled requirement",
                                        json!({"case":result}),
                                    )),
                                    _ => Err(Fault::unknown(
                                        "The property outcome could not be verified",
                                    )),
                                }
                            },
                        )?;
                    }
                    Ok(json!({"status":"PASS"}))
                })();
                self.active = false;
                outcome
            }
            "graph.plan" => {
                let model = Model::new(params["model"].clone())?;
                let options: Options =
                    serde_json::from_value(params.get("options").cloned().unwrap_or(json!({})))
                        .map_err(|e| Fault::unknown(format!("Invalid core options: {e}")))?;
                options.validate()?;
                Ok(json!(graph::path(&model, options.seed, options.max_steps)?))
            }
            "run.plan" => {
                let model = Model::new(params["model"].clone())?;
                let options: Options =
                    serde_json::from_value(params.get("options").cloned().unwrap_or(json!({})))
                        .map_err(|e| Fault::unknown(format!("Invalid core options: {e}")))?;
                engine::inventory(&model, &options)
            }
            "run.start" | "case.replay" => {
                if self.active {
                    return Err(Fault::unknown(
                        "This JSON-RPC worker already has an active run",
                    ));
                }
                let model = Model::new(params["model"].clone())?;
                let options: Options =
                    serde_json::from_value(params.get("options").cloned().unwrap_or(json!({})))
                        .map_err(|e| Fault::unknown(format!("Invalid core options: {e}")))?;
                let replay = params.get("case").filter(|c| !c.is_null()).cloned();
                if name == "case.replay" && replay.is_none() {
                    return Err(Fault::unknown("Supply a replay case"));
                }
                self.active = true;
                self.cancelled = false;
                let result = engine::run(
                    &model,
                    self,
                    options,
                    params.get("target").cloned().unwrap_or(json!({})),
                    replay,
                );
                self.active = false;
                if let Ok(mut report) = result {
                    if let Some(decisions) = &self.decisions {
                        report["usage"] = decisions.stats();
                        // Transcripts grow independently of the execution report.
                        // Retrieve them through bounded pages, never one giant RPC.
                        report["decision_audit"] =
                            json!({"method":"decision.audit","total":decisions.audit.len()});
                    }
                    self.results = report.clone();
                    Ok(report)
                } else {
                    result
                }
            }
            "run.cancel" => {
                self.cancelled = self.active;
                Ok(json!({"cancelled":self.cancelled}))
            }
            "run.results" => Ok(self.results.clone()),
            "decision.configure" => {
                if self.active {
                    return Err(Fault::unknown(
                        "Configure a decision provider before starting a run",
                    ));
                }
                if params["provider"].as_str().unwrap_or("jev") != "jev" {
                    return Err(Fault::unknown(
                        "This host currently ships the Jev provider; native integrations can supply DecisionProvider implementations",
                    ));
                }
                let calls = params
                    .get("max_calls")
                    .map(|v| {
                        v.as_u64()
                            .ok_or_else(|| Fault::unknown("max_calls must be an integer"))
                    })
                    .transpose()?
                    .unwrap_or(1000);
                let calls = usize::try_from(calls)
                    .map_err(|_| Fault::unknown("Invalid model-call budget"))?;
                let threshold = params
                    .get("threshold")
                    .map(|v| {
                        v.as_f64()
                            .ok_or_else(|| Fault::unknown("threshold must be a number"))
                    })
                    .transpose()?
                    .unwrap_or(0.85);
                self.decisions = Some(Decisions::new(
                    Box::new(JevProvider::new(params)?),
                    calls,
                    threshold,
                )?);
                Ok(json!({}))
            }
            "decision.ask" => self
                .decisions
                .as_mut()
                .ok_or_else(|| Fault::unknown("Configure a decision provider first"))?
                .ask(&params["evidence"], &params["questions"]),
            "decision.post" => self
                .decisions
                .as_mut()
                .ok_or_else(|| Fault::unknown("Configure a decision provider first"))?
                .post(
                    string(&params["url"]),
                    string(&params["key"]),
                    &params["body"],
                    params["cache"].as_bool().unwrap_or(true),
                ),
            "decision.answer" => {
                let answer = self
                    .decisions
                    .as_ref()
                    .ok_or_else(|| Fault::unknown("Configure a decision provider first"))?
                    .answer(
                        &params["result"],
                        string(&params["name"]),
                        &params["choices"],
                        params["threshold"].as_f64(),
                    )?;
                Ok(json!(answer))
            }
            "decision.stats" => {
                Ok(self.decisions.as_ref().map(Decisions::stats).unwrap_or(
                    json!({"calls":0,"cache_hits":0,"input_tokens":0,"output_tokens":0}),
                ))
            }
            "decision.audit" => {
                let offset = params
                    .get("offset")
                    .map(|v| {
                        v.as_u64()
                            .ok_or_else(|| Fault::unknown("Audit offset must be an integer"))
                    })
                    .transpose()?
                    .unwrap_or(0);
                let limit = params
                    .get("limit")
                    .map(|v| {
                        v.as_u64()
                            .ok_or_else(|| Fault::unknown("Audit limit must be an integer"))
                    })
                    .transpose()?
                    .unwrap_or(128);
                if !(1..=1024).contains(&limit) {
                    return Err(Fault::unknown("Audit limit must be from 1 through 1024"));
                }
                let entries = self
                    .decisions
                    .as_ref()
                    .map(|d| d.audit.as_slice())
                    .unwrap_or(&[]);
                let offset = usize::try_from(offset)
                    .map_err(|_| Fault::unknown("Audit offset is too large"))?;
                if offset > entries.len() {
                    return Err(Fault::unknown("Audit offset exceeds the transcript length"));
                }
                let mut page = Vec::new();
                let mut bytes = 0;
                for entry in entries.iter().skip(offset).take(limit as usize) {
                    let size = serde_json::to_vec(entry)
                        .map_err(|_| Fault::unknown("Cannot serialize audit entry"))?
                        .len();
                    if size > MAX_MESSAGE / 2 {
                        return Err(Fault::unknown(
                            "A decision audit entry exceeds the JSON-RPC size limit",
                        ));
                    }
                    if !page.is_empty() && bytes + size > 4 * 1024 * 1024 {
                        break;
                    }
                    bytes += size;
                    page.push(entry.clone());
                }
                let next = offset + page.len();
                Ok(
                    json!({"entries":page,"total":entries.len(),"next_offset":if next < entries.len() {Some(next)} else {None}}),
                )
            }
            _ => Err(Fault {
                status: "INCONCLUSIVE".into(),
                message: format!("Unknown JSON-RPC method: {name}"),
                result: json!({"method_not_found":true}),
            }),
        }
    }
    fn handle(&mut self, request: &Value) -> Result<(), Fault> {
        let valid_id = |id: &Value| id.is_string() || id.is_number() || id.is_null();
        if !request.is_object()
            || request["jsonrpc"] != "2.0"
            || !request["method"].is_string()
            || request.get("id").is_some_and(|id| !valid_id(id))
        {
            // An invalid envelope is not a notification. A missing or invalid
            // identifier cannot safely be echoed back to the client.
            let id = request.get("id").filter(|id| valid_id(id));
            return self.write(&json!({
                "jsonrpc":"2.0", "id":id,
                "error":{"code":-32600,"message":"Invalid JSON-RPC request"}
            }));
        }
        if request
            .get("params")
            .is_some_and(|params| !params.is_object() && !params.is_array())
        {
            // Valid notifications never receive a response, including errors
            // produced while validating their parameters.
            if let Some(id) = request.get("id") {
                self.write(&json!({
                    "jsonrpc":"2.0", "id":id,
                    "error":{"code":-32602,"message":"JSON-RPC params must be an object or array"}
                }))?;
            }
            return Ok(());
        }
        let result = self.method(
            string(&request["method"]),
            request.get("params").unwrap_or(&json!({})),
        );
        self.respond(request, result)
    }
    pub fn serve(&mut self) -> Result<(), Fault> {
        while !self.shutdown {
            match self.read() {
                Ok(Some(request)) => self.handle(&request)?,
                Ok(None) => break,
                Err(error) => {
                    self.write(&json!({"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":error.message}}))?;
                }
            }
        }
        Ok(())
    }
}
impl<R: BufRead, W: Write> Target for Peer<R, W> {
    fn call(&mut self, method: &str, params: Value) -> Result<Value, Fault> {
        if self.cancelled && !["target.close", "target.hook", "run.event"].contains(&method) {
            return Err(Fault::unknown("Run cancelled"));
        }
        if method.starts_with("decision.") {
            return self.method(method, &params);
        }
        self.sequence += 1;
        let id = format!("core/{}", self.sequence);
        self.write(&json!({"jsonrpc":"2.0","id":id,"method":method,"params":params}))?;
        loop {
            let message = self
                .read()?
                .ok_or_else(|| Fault::unknown("The target adapter disconnected"))?;
            if message.get("method").is_some() {
                self.handle(&message)?;
                continue;
            }
            if message["jsonrpc"] != "2.0" || message["id"] != id {
                return Err(Fault::unknown("Mismatched JSON-RPC adapter response"));
            }
            if let Some(error) = message.get("error") {
                let mut fault: Fault = serde_json::from_value(error["data"].clone())
                    .unwrap_or_else(|_| {
                        Fault::unknown(error["message"].as_str().unwrap_or("Target adapter error"))
                    });
                if fault.status != "FAIL" {
                    fault.status = "INCONCLUSIVE".into();
                }
                return Err(fault);
            }
            return message
                .get("result")
                .cloned()
                .ok_or_else(|| Fault::unknown("Missing JSON-RPC adapter result"));
        }
    }
}
