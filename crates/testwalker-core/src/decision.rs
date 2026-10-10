//! Provider I/O, confidence policy, caching and budgets live in the core.
use crate::{Fault, model::string};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::HashMap, time::Duration};

pub const JEV_ENDPOINT: &str = "https://api.typesafe.ai/v1/systemone";

/// Additional providers implement this interface without changing the engine.
pub trait DecisionProvider {
    fn ask(&mut self, evidence: &Value, questions: &Value) -> Result<Value, Fault>;
    fn post(&mut self, _url: &str, _key: &str, _body: &Value) -> Result<Value, Fault> {
        Err(Fault::unknown(
            "This provider does not support an auxiliary text helper",
        ))
    }
}

pub struct JevProvider {
    http: reqwest::blocking::Client,
    api_key: String,
    model: String,
    endpoint: String,
}
impl JevProvider {
    pub fn new(config: &Value) -> Result<Self, Fault> {
        let http = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(30))
            .connect_timeout(Duration::from_secs(10))
            .build()
            .map_err(|_| Fault::unknown("Cannot initialize the decision provider"))?;
        let api_key = string(&config["api_key"]).to_owned();
        let model = config["model"].as_str().unwrap_or("jev-latest").to_owned();
        let endpoint = config["endpoint"]
            .as_str()
            .unwrap_or(JEV_ENDPOINT)
            .to_owned();
        Ok(Self {
            http,
            api_key,
            model,
            endpoint,
        })
    }
}
impl DecisionProvider for JevProvider {
    fn ask(&mut self, evidence: &Value, questions: &Value) -> Result<Value, Fault> {
        if self.api_key.trim().is_empty() {
            return Err(Fault::unknown(
                "Supply TYPESAFE_API_KEY in the properties file",
            ));
        }
        self.post(
            &self.endpoint.clone(),
            &self.api_key.clone(),
            &json!({"model":self.model,"state":evidence,"questions":questions}),
        )
    }
    fn post(&mut self, url: &str, key: &str, body: &Value) -> Result<Value, Fault> {
        if key.trim().is_empty() {
            return Err(Fault::unknown(
                "Supply an API key for the configured text helper",
            ));
        }
        let response = self
            .http
            .post(url)
            .bearer_auth(key)
            .json(body)
            .send()
            .map_err(|_| Fault::unknown("The decision provider connection failed or timed out"))?;
        if !response.status().is_success() {
            return Err(Fault::unknown(format!(
                "The decision provider failed with HTTP {}",
                response.status().as_u16()
            )));
        }
        let value: Value = response
            .json()
            .map_err(|_| Fault::unknown("The decision provider returned invalid JSON"))?;
        if !value.is_object() {
            return Err(Fault::unknown(
                "The decision provider response is not an object",
            ));
        }
        Ok(value)
    }
}

pub struct Decisions {
    pub provider: Box<dyn DecisionProvider>,
    pub threshold: f64,
    pub max_calls: usize,
    cache: HashMap<String, Value>,
    pub calls: usize,
    pub cache_hits: usize,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub audit: Vec<Value>,
}
impl Decisions {
    pub fn new(
        provider: Box<dyn DecisionProvider>,
        max_calls: usize,
        threshold: f64,
    ) -> Result<Self, Fault> {
        if !(1..=10000).contains(&max_calls)
            || !threshold.is_finite()
            || threshold <= 0.5
            || threshold > 1.0
        {
            return Err(Fault::unknown(
                "Use a call budget from 1–10000 and confidence above 0.5 through 1",
            ));
        }
        Ok(Self {
            provider,
            threshold,
            max_calls,
            cache: HashMap::new(),
            calls: 0,
            cache_hits: 0,
            input_tokens: 0,
            output_tokens: 0,
            audit: Vec::new(),
        })
    }
    pub fn ask(&mut self, evidence: &Value, questions: &Value) -> Result<Value, Fault> {
        let request = json!({"state":evidence,"questions":questions});
        let mut audit = json!({"request":request});
        let digest = format!("{:x}", Sha256::digest(request.to_string()));
        let result = if let Some(cached) = self.cache.get(&digest) {
            self.cache_hits += 1;
            audit["cache_hit"] = json!(true);
            Ok(cached.clone())
        } else if self.calls >= self.max_calls {
            Err(Fault::unknown("The model-call budget is exhausted"))
        } else {
            self.calls += 1;
            let response = self.provider.ask(evidence, questions);
            if let Ok(value) = &response {
                self.input_tokens += value["usage"]["input_tokens"].as_u64().unwrap_or(0);
                self.output_tokens += value["usage"]["output_tokens"].as_u64().unwrap_or(0);
                self.cache.insert(digest, value.clone());
            }
            response
        };
        match &result {
            Ok(value) => audit["response"] = value.clone(),
            Err(error) => audit["error"] = json!(error.message),
        }
        self.audit.push(audit);
        result
    }
    pub fn answer(
        &self,
        result: &Value,
        name: &str,
        choices: &Value,
        threshold: Option<f64>,
    ) -> Result<String, Fault> {
        let fail = || Fault::unknown(format!("Invalid or refused Jev answer for {name}"));
        let answer = &result["answers"][name];
        let choice = answer["choice"].as_str().ok_or_else(fail)?;
        let criteria: std::collections::HashSet<&str> = if let Some(map) = choices.as_object() {
            map.keys().map(String::as_str).collect()
        } else if let Some(array) = choices.as_array() {
            let keys = array
                .iter()
                .map(|v| v.as_str().ok_or_else(fail))
                .collect::<Result<Vec<_>, _>>()?;
            let set = keys
                .iter()
                .copied()
                .collect::<std::collections::HashSet<_>>();
            if set.len() != keys.len() {
                return Err(fail());
            }
            set
        } else {
            return Err(fail());
        };
        let probabilities = answer["probabilities"].as_object().ok_or_else(fail)?;
        let confidence = answer["confidence"]
            .as_f64()
            .filter(|n| n.is_finite() && (0.0..=1.0).contains(n))
            .ok_or_else(fail)?;
        if !criteria.contains(choice)
            || probabilities.len() != criteria.len()
            || !criteria.iter().all(|k| {
                probabilities
                    .get(*k)
                    .and_then(Value::as_f64)
                    .is_some_and(|n| n.is_finite() && (0.0..=1.0).contains(&n))
            })
        {
            return Err(fail());
        }
        let sum: f64 = probabilities.values().filter_map(Value::as_f64).sum();
        let chosen = probabilities[choice].as_f64().unwrap();
        if (sum - 1.0).abs() >= 0.02
            || probabilities
                .values()
                .filter_map(Value::as_f64)
                .any(|n| n > chosen + 1e-6)
        {
            return Err(fail());
        }
        let threshold = threshold.unwrap_or(self.threshold);
        if !threshold.is_finite() || threshold <= 0.5 || threshold > 1.0 {
            return Err(Fault::unknown("Invalid decision confidence threshold"));
        }
        if confidence < threshold || probabilities[choice].as_f64().unwrap() < threshold {
            let mut error = Fault::unknown(format!(
                "Jev could not confidently resolve {name}: choice={choice}, confidence={confidence:.2}, threshold={threshold:.2}"
            ));
            error.result = json!({"uncertain_decision":true,"answer":answer});
            return Err(error);
        }
        Ok(choice.to_owned())
    }
    pub fn post(
        &mut self,
        url: &str,
        key: &str,
        body: &Value,
        cache: bool,
    ) -> Result<Value, Fault> {
        let digest = format!("text/{:x}", Sha256::digest(format!("{url}{body}")));
        if cache && let Some(value) = self.cache.get(&digest) {
            self.cache_hits += 1;
            return Ok(value.clone());
        }
        if self.calls >= self.max_calls {
            return Err(Fault::unknown("The model-call budget is exhausted"));
        }
        self.calls += 1;
        let result = self.provider.post(url, key, body)?;
        self.input_tokens += result["usage"]["input_tokens"].as_u64().unwrap_or(0);
        self.output_tokens += result["usage"]["output_tokens"].as_u64().unwrap_or(0);
        if cache {
            self.cache.insert(digest, result.clone());
        }
        Ok(result)
    }
    pub fn stats(&self) -> Value {
        json!({"calls":self.calls,"cache_hits":self.cache_hits,"input_tokens":self.input_tokens,"output_tokens":self.output_tokens})
    }
}
