//! Native GraphWalker planning. Planned visits are never verified coverage.
use crate::{
    Fault,
    model::{Model, string},
};
use graphwalker_core::{
    machine::{ExecutionContext, Machine},
    model::ElementIndex,
};
use graphwalker_dsl::generator::parse_generator;
use serde_json::{Value, json};

pub fn path(model: &Model, seed: u64, max_steps: usize) -> Result<Vec<Value>, Fault> {
    let contexts = graphwalker_io::json::read_json_string(&model.document.to_string())
        .map_err(|e| Fault::unknown(format!("GraphWalker model: {e}")))?;
    let mut entries = Vec::new();
    for context in contexts {
        let generator = parse_generator(
            context
                .generator
                .as_deref()
                .ok_or_else(|| Fault::unknown("Missing graph generator"))?,
        )
        .map_err(|e| Fault::unknown(format!("GraphWalker generator: {e}")))?;
        let mut execution = ExecutionContext::new_with_seed(context.model, seed);
        if let Some(id) = context.start_element_id {
            let element = execution
                .model()
                .element_by_id(&id)
                .ok_or_else(|| Fault::unknown("Missing start element"))?;
            execution.set_next_element(Some(element));
        }
        entries.push((execution, generator));
    }
    let mut machine = Machine::new_with_seed(entries, seed)
        .map_err(|e| Fault::unknown(format!("GraphWalker: {e}")))?;
    machine.set_record_path(false);
    let mut path = Vec::new();
    while machine.has_next_step() {
        if path.len() >= max_steps {
            return Err(Fault::unknown(format!(
                "GraphWalker route exceeds --max-steps {max_steps}; increase the limit or choose a bounded generator"
            )));
        }
        machine
            .get_next_step()
            .map_err(|e| Fault::unknown(format!("GraphWalker: {e}")))?;
        let context = machine.context(machine.current_context_index());
        let Some(element) = context.current_element() else {
            return Err(Fault::unknown("GraphWalker produced no current element"));
        };
        let (id, kind) = match element {
            ElementIndex::Vertex(i) => (context.model().vertex(i).id(), "state"),
            ElementIndex::Edge(i) => (context.model().edge(i).id(), "edge"),
        };
        let mut item = if kind == "state" {
            model.states[id].clone()
        } else {
            model.edges[id].clone()
        };
        item["kind"] = json!(kind);
        path.push(item);
    }
    // Stopping on an edge still requires a destination verification checkpoint.
    if let Some(last) = path.last().filter(|e| e["kind"] == "edge") {
        if path.len() >= max_steps {
            return Err(Fault::unknown(
                "The final destination requires another verification checkpoint",
            ));
        }
        let mut state = model.states[string(&last["targetVertexId"])].clone();
        state["kind"] = json!("state");
        path.push(state);
    }
    validate_path(model, &path)?;
    Ok(path)
}
pub fn canonical_path(model: &Model, route: &[Value]) -> Result<Vec<Value>, Fault> {
    let mut path = Vec::new();
    for item in route {
        let id = string(&item["id"]);
        let element = if item["kind"] == "state" {
            model.states.get(id)
        } else if item["kind"] == "edge" {
            model.edges.get(id)
        } else {
            None
        };
        let mut element = element
            .cloned()
            .ok_or_else(|| Fault::unknown("Unknown imported route element"))?;
        element["kind"] = item["kind"].clone();
        path.push(element);
    }
    validate_path(model, &path)?;
    Ok(path)
}
pub fn validate_path(model: &Model, path: &[Value]) -> Result<(), Fault> {
    if path
        .first()
        .is_none_or(|e| e["id"] != model.start() || e["kind"] != "state")
    {
        return Err(Fault::unknown(
            "A planned route must begin at the model's start state",
        ));
    }
    let mut current = model.start();
    let mut expecting_edge = true;
    for item in path.iter().skip(1) {
        if expecting_edge {
            let edge = model
                .edges
                .get(string(&item["id"]))
                .ok_or_else(|| Fault::unknown("Unknown route edge"))?;
            if item["kind"] != "edge" || edge["sourceVertexId"] != current {
                return Err(Fault::unknown("Disconnected GraphWalker route"));
            }
            current = string(&edge["targetVertexId"]);
        } else if item["kind"] != "state" || item["id"] != current {
            return Err(Fault::unknown("Incorrect route checkpoint"));
        }
        expecting_edge = !expecting_edge;
    }
    if !expecting_edge {
        return Err(Fault::unknown(
            "Route ends without a verification checkpoint",
        ));
    }
    Ok(())
}
