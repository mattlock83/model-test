"use strict";
const report = JSON.parse(document.getElementById("run-data").textContent);
const tests = report.tests || [],
  states = report.graph?.states || [],
  edges = report.graph?.edges || [];
const byId = new Map(tests.map((t) => [t.id, t])),
  edgeById = new Map(edges.map((e) => [e.id, e]));
const colors = {
  PASS: "#168269",
  FAIL: "#c74451",
  INCONCLUSIVE: "#b27914",
  SKIPPED: "#84949d",
  UNSELECTED: "#d1dce2",
};
const priorities = ["FAIL", "INCONCLUSIVE", "PASS", "SKIPPED"];
let selected =
  tests.find((t) => ["FAIL", "INCONCLUSIVE"].includes(t.status))?.id ||
  tests[0]?.id;
let focus = null,
  attemptIndex = null,
  zoom = 1;
const $ = (id) => document.getElementById(id);
const json = (value) => JSON.stringify(value, null, 2);
function element(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function badge(status) {
  return element("span", status, "badge " + status);
}
function edgeId(test) {
  return test.kind === "graph" && test.element?.kind === "edge"
    ? test.element.id
    : test.journey;
}
function outcome(items) {
  return (
    priorities.find((s) => items.some((t) => t.status === s)) || "UNSELECTED"
  );
}
function related(test) {
  const e = edgeById.get(edgeId(test));
  return new Set(
    [
      e?.source,
      e?.target,
      test.expected_state,
      test.element?.kind === "state" ? test.element.id : null,
      test.accepted_at,
      test.rejected_at,
    ].filter(Boolean),
  );
}
function details(parent, title, data) {
  const box = element("details");
  const body = element("pre");
  let loaded = false;
  box.append(element("summary", title), body);
  box.addEventListener("toggle", async () => {
    if (!box.open || loaded) return;
    loaded = true;
    body.textContent = "Loading evidence…";
    try {
      body.textContent = json(typeof data === "function" ? await data() : data);
    } catch (error) {
      loaded = false;
      body.textContent = `${error.message} Keep the evidence folder beside report.html. Full evidence is also in report.json. Close and reopen this section to retry.`;
    }
  });
  parent.append(box);
}
window.testwalkerDecisionChunks = {};
const decisionLoads = new Map();
function loadDecisionChunk(index) {
  if (!decisionLoads.has(index)) {
    const promise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = report.decision_chunks.files[index];
      script.onload = () => {
        script.remove();
        const data = window.testwalkerDecisionChunks[index];
        if (Array.isArray(data)) resolve(data);
        else reject(new Error("Decision evidence is invalid."));
      };
      script.onerror = () => {
        script.remove();
        reject(new Error("Decision evidence could not be loaded."));
      };
      document.head.append(script);
    }).catch((error) => {
      decisionLoads.delete(index);
      throw error;
    });
    decisionLoads.set(index, promise);
  }
  return decisionLoads.get(index);
}
async function decisionEvidence(start, end) {
  if (!report.decision_chunks) return (report.decisions || []).slice(start, end);
  if (end <= start) return [];
  const size = report.decision_chunks.size;
  const first = Math.floor(start / size), last = Math.floor((end - 1) / size);
  const chunks = await Promise.all(
    Array.from({ length: last - first + 1 }, (_, i) => loadDecisionChunk(first + i)),
  );
  return chunks.flat().slice(start - first * size, end - first * size);
}
function choose(id) {
  selected = id;
  attemptIndex = null;
  renderCases();
  renderGraph();
  renderDetail();
}
function visibleTests() {
  const text = $("search").value.toLowerCase();
  return tests.filter(
    (t) =>
      (!$("status-filter").value || t.status === $("status-filter").value) &&
      (!$("kind-filter").value || t.kind === $("kind-filter").value) &&
      (!text ||
        [t.name, t.id, t.intent, t.data_set, t.phase?.source]
          .filter(Boolean)
          .join(" ")
          .toLowerCase()
          .includes(text)) &&
      (!focus ||
        (focus.kind === "state"
          ? related(t).has(focus.id)
          : edgeId(t) === focus.id)),
  );
}
function renderCases() {
  const matches = visibleTests();
  $("case-count").textContent = matches.length + " / " + tests.length;
  $("case-list").replaceChildren();
  if (!matches.length) {
    $("case-list").append(
      element("div", "No cases match these filters.", "empty"),
    );
    return;
  }
  for (const t of matches) {
    const button = element(
      "button",
      undefined,
      "case" + (t.id === selected ? " selected" : ""),
    );
    button.type = "button";
    button.dataset.testId = t.id;
    button.setAttribute("aria-pressed", String(t.id === selected));
    const label = element("span", undefined, "case-label");
    label.append(
      element("span", t.name || t.id, "case-name"),
      element(
        "span",
        t.kind === "graph"
          ? `Graph · walk ${t.walk} · step ${t.position}`
          : `${t.data_set || "Replay"} · ${(t.attempt_indices || []).filter((i) => report.cases[i]?.attempted !== false).length} input attempts`,
        "case-meta",
      ),
    );
    button.append(badge(t.status), label);
    button.addEventListener("click", () => choose(t.id));
    $("case-list").append(button);
  }
}
function filterGraph(kind, id) {
  focus = { kind, id };
  const matches = visibleTests();
  if (matches.length && !matches.some((t) => t.id === selected)) {
    selected =
      matches.find((t) => ["FAIL", "INCONCLUSIVE"].includes(t.status))?.id ||
      matches[0].id;
    attemptIndex = null;
  }
  $("graph-note").textContent =
    kind === "state"
      ? `Cases connected to ${states.find((s) => s.id === id)?.name || id}.`
      : `Journeys on ${edgeById.get(id)?.name || id}.`;
  renderCases();
  renderGraph();
  renderDetail();
}
function svg(tag, attrs = {}, text) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
}
function renderGraph() {
  const canvas = $("graph");
  canvas.style.width = `${zoom * 100}%`;
  canvas.style.height = `${$("graph-viewport").clientHeight * zoom}px`;
  canvas.replaceChildren();
  $("graph-count").textContent =
    `${states.length} states · ${edges.length} journeys`;
  if (!states.length) {
    canvas.append(
      svg(
        "text",
        { x: 20, y: 40, fill: colors.SKIPPED },
        "Graph data is unavailable for this report.",
      ),
    );
    return;
  }
  const columns = Math.min(4, Math.ceil(Math.sqrt(states.length))),
    rows = Math.ceil(states.length / columns),
    width = columns * 180 + 40,
    height = rows * 105 + 55;
  canvas.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const positions = new Map(
    states.map((s, i) => [
      s.id,
      { x: 110 + (i % columns) * 180, y: 62 + Math.floor(i / columns) * 105 },
    ]),
  );
  const defs = svg("defs"),
    marker = svg("marker", {
      id: "arrow",
      viewBox: "0 0 10 10",
      refX: 9,
      refY: 5,
      markerWidth: 6,
      markerHeight: 6,
      orient: "auto-start-reverse",
    });
  marker.append(
    svg("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "context-stroke" }),
  );
  defs.append(marker);
  canvas.append(defs);
  const current = byId.get(selected),
    chosen = edgeId(current || {}),
    near = current ? related(current) : new Set();
  for (const [index, e] of edges.entries()) {
    const a = positions.get(e.source),
      b = positions.get(e.target);
    if (!a || !b) continue;
    const isSelected = e.id === chosen,
      incident =
        focus?.kind === "state"
          ? e.source === focus.id || e.target === focus.id
          : !chosen && (near.has(e.source) || near.has(e.target));
    if (!$("all-edges").checked && !isSelected && !incident) continue;
    const results = tests.filter(
        (t) => t.kind === "graph" && edgeId(t) === e.id,
      ),
      status = outcome(results),
      dx = b.x - a.x,
      dy = b.y - a.y,
      len = Math.hypot(dx, dy) || 1;
    const start = { x: a.x + (dx / len) * 43, y: a.y + (dy / len) * 25 },
      end = { x: b.x - (dx / len) * 43, y: b.y - (dy / len) * 25 };
    const bend = ((index % 3) - 1) * 20 + 18,
      control = {
        x: (a.x + b.x) / 2 - (dy / len) * bend,
        y: (a.y + b.y) / 2 + (dx / len) * bend,
      };
    const d =
      e.source === e.target
        ? `M ${a.x - 20} ${a.y - 22} C ${a.x - 55} ${a.y - 73}, ${a.x + 65} ${a.y - 73}, ${a.x + 24} ${a.y - 22}`
        : `M ${start.x} ${start.y} Q ${control.x} ${control.y} ${end.x} ${end.y}`;
    const group = svg("g");
    const line = svg("path", {
      d,
      stroke: isSelected ? "#245fc4" : colors[status],
      "stroke-width": isSelected ? 3 : 1.5,
      opacity: isSelected || incident ? 1 : 0.28,
      "marker-end": "url(#arrow)",
      class: "edge",
    });
    line.append(
      svg("title", {}, `${e.name}: ${e.source} → ${e.target} · ${status}`),
    );
    const hit = svg("path", {
      d,
      stroke: "transparent",
      "stroke-width": 12,
      class: "edge",
      tabindex: 0,
      role: "button",
      "aria-label": `Filter journey ${e.name}`,
    });
    hit.addEventListener("click", () => filterGraph("edge", e.id));
    hit.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        filterGraph("edge", e.id);
      }
    });
    group.append(line, hit);
    canvas.append(group);
  }
  for (const s of states) {
    const p = positions.get(s.id),
      results = tests.filter(
        (t) => t.kind === "graph" && t.expected_state === s.id,
      ),
      status = outcome(results),
      active = near.has(s.id) || focus?.id === s.id;
    const group = svg("g", {
      class: "node",
      tabindex: 0,
      role: "button",
      "aria-label": `Filter state ${s.name}`,
    });
    group.append(
      svg(
        "title",
        {},
        `${s.name}\n${s.description || ""}\n${status} · ${results.length} planned checks`,
      ),
    );
    group.append(
      svg("rect", {
        x: p.x - 76,
        y: p.y - 25,
        width: 152,
        height: 50,
        rx: 9,
        fill: active ? "#eff4ff" : "#fff",
        stroke: active ? "#245fc4" : "#d8e2e7",
        "stroke-width": active ? 2 : 1,
      }),
    );
    group.append(
      svg("circle", { cx: p.x - 61, cy: p.y - 6, r: 4, fill: colors[status] }),
    );
    const name = s.name.replaceAll("_", " ");
    group.append(
      svg(
        "text",
        {
          x: p.x - 49,
          y: p.y - 2,
          "font-size": 11,
          "font-family": "system-ui",
          fill: "#203944",
        },
        name.length > 21 ? name.slice(0, 20) + "…" : name,
      ),
    );
    group.append(
      svg(
        "text",
        {
          x: p.x - 61,
          y: p.y + 15,
          "font-size": 8,
          "font-family": "system-ui",
          fill: "#75858e",
        },
        s.id === report.graph.start ? "START · " + status : status,
      ),
    );
    group.addEventListener("click", () => filterGraph("state", s.id));
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        filterGraph("state", s.id);
      }
    });
    canvas.append(group);
  }
}
function renderDetail() {
  const t = byId.get(selected),
    parent = $("detail");
  parent.replaceChildren();
  if (!t) {
    parent.append(
      element(
        "div",
        "No planned cases are available. Inspect the run stop details below.",
        "empty",
      ),
    );
    return;
  }
  $("detail-status").textContent = t.status;
  $("detail-status").className = "badge " + t.status;
  parent.append(
    element("h3", t.name || t.id, "detail-title"),
    element("div", t.id, "muted"),
  );
  if (t.reason) parent.append(element("div", t.reason, "reason"));
  const attempts = (t.attempt_indices || []).map((i) => report.cases[i]);
  let record = t;
  if (attempts.length) {
    if (attemptIndex === null || attemptIndex >= attempts.length) {
      const lastFailure = t.status === "FAIL"
        ? attempts.map((attempt) => attempt.status).lastIndexOf("FAIL")
        : -1;
      attemptIndex = lastFailure >= 0 ? lastFailure : attempts.length - 1;
    }
    record = attempts[attemptIndex];
    const line = element("label", undefined, "attempt-line");
    line.append(element("span", "Input attempt"));
    const select = element("select");
    select.id = "attempt";
    select.setAttribute("aria-label", "Choose property input attempt");
    attempts.forEach((a, i) => {
      const opt = element(
        "option",
        `${i + 1} / ${attempts.length} · ${a.status}${a.attempted === false ? " · not executed" : ""}`,
      );
      opt.value = i;
      select.append(opt);
    });
    select.value = attemptIndex;
    select.addEventListener("change", () => {
      attemptIndex = Number(select.value);
      renderDetail();
    });
    line.append(select);
    parent.append(line);
    parent.append(
      element(
        "div",
        record.attempted === false
          ? "This input was not executed; the run budget was exhausted."
          : `Attempt outcome: ${record.status}. Generated phases may include failure reproduction and shrinking.`,
        "muted",
      ),
    );
    if (record.error) parent.append(element("p", record.error, "reason"));
    if (record.minimization_note)
      parent.append(element("p", record.minimization_note, "reason"));
  }
  const facts = element("dl", undefined, "facts");
  for (const [label, value] of [
    ["Intent", t.intent],
    [
      "Expected",
      t.expected_state ||
        record.expected ||
        (record.violations?.length ? t.rejected_at : t.accepted_at),
    ],
    [
      "Duration",
      t.duration === undefined ? null : t.duration.toFixed(2) + " s",
    ],
    ["Captured", record.screenshot_at],
  ]) {
    if (value !== undefined && value !== null) {
      facts.append(element("dt", label), element("dd", value));
    }
  }
  parent.append(facts);
  const shot = record.screenshot,
    figure = element("figure", undefined, "snapshot");
  if (
    record.attempted !== false &&
    typeof shot === "string" &&
    /^screenshots\/[0-9]+\.png$/.test(shot)
  ) {
    const link = element("a");
    link.href = shot;
    link.target = "_blank";
    link.rel = "noopener";
    link.title = "Open full-size screenshot";
    const img = element("img");
    img.src = shot;
    img.alt = `Browser viewport for ${t.name || t.id}${attempts.length ? " input attempt " + (attemptIndex + 1) : ""}`;
    img.loading = "lazy";
    img.addEventListener("error", () => {
      figure.replaceChildren(
        element(
          "div",
          "The screenshot file is unavailable. Keep the screenshots folder beside this report.",
          "empty",
        ),
      );
    });
    link.append(img);
    figure.append(
      link,
      element(
        "figcaption",
        "Captured after execution · click to open full size",
      ),
    );
  } else {
    let note =
      t.status === "SKIPPED" || record.attempted === false
        ? "Not executed — no screenshot."
        : report.limits?.screenshots === false
          ? "Screenshots were disabled for this run."
          : "No screenshot was captured.";
    if (record.screenshot_error) note += " " + record.screenshot_error;
    figure.append(element("div", note, "empty"));
  }
  parent.append(figure);
  if (record.input || t.input || t.phase?.input)
    details(parent, "Test input", record.input || t.input || t.phase.input);
  const checkpoints = (t.step_indices || []).map((i) => report.steps[i]);
  if (t.description || t.rules || t.expected_states)
    details(parent, "Expected behavior", {
      description: t.description,
      rules: t.rules,
      states: t.expected_states,
      global_rules: t.global_rules,
    });
  if (record.checks || checkpoints.length)
    details(
      parent,
      "Observed checkpoints & rules",
      record.checks ? record : checkpoints,
    );
  if (t.status === "SKIPPED")
    details(parent, "Why this case was skipped", {
      reason: t.reason,
      stop: report.stop,
      upstream: tests
        .filter((x) => ["FAIL", "INCONCLUSIVE"].includes(x.status))
        .map((x) => ({ id: x.id, reason: x.reason })),
    });
  const trace = t.trace_range || [0, 0],
    decisions = t.decision_range || [0, 0];
  details(parent, "Actions & Jev decisions", async () => ({
    actions: (report.trace || []).slice(...trace),
    decisions: await decisionEvidence(...decisions),
  }));
  details(parent, "Complete case evidence", {
    test: t,
    attempt: attempts.length ? record : undefined,
  });
}
$("model-title").textContent = report.model;
$("run-status").textContent = report.status;
$("run-status").className = "badge " + report.status;
$("run-meta").textContent =
  `${report.started} · seed ${report.seed} · ${report.url}`;
const propertyPhases = report.coverage?.["property phases"];
if (propertyPhases) {
  const progress = $("property-progress");
  progress.hidden = false;
  progress.textContent =
    `Selected property phases passed: ${propertyPhases.completed}/${propertyPhases.total}`;
  const campaigns = report.coverage?.properties;
  if (campaigns)
    progress.textContent +=
      ` · Full property campaigns completed: ${campaigns.completed}/${campaigns.total}`;
}
const count = (status) => tests.filter((t) => t.status === status).length;
for (const [value, label] of [
  [
    `${report.coverage?.edges?.verified || 0}/${report.coverage?.edges?.total || 0}`,
    "Verified graph journeys",
  ],
  [count("PASS"), "Passed checks"],
  [count("FAIL"), "Failed checks"],
  [count("INCONCLUSIVE"), "Inconclusive checks"],
  [count("SKIPPED"), "Skipped checks"],
  [report.usage?.calls || 0, "Jev calls"],
]) {
  const card = element("div", undefined, "metric");
  card.append(element("strong", value), element("span", label));
  $("metrics").append(card);
}
if (report.error) {
  $("stop").hidden = false;
  $("stop").textContent = `${report.status}: ${report.error}`;
}
$("stop-detail").textContent = json(
  report.stop || {
    status: report.status,
    message: "No early stop recorded.",
    cleanup_errors: report.cleanup_errors || [],
  },
);
$("run-detail").textContent = json({
  coverage: report.coverage,
  limits: report.limits,
  planning: report.planning,
  scope: report.scope,
});
$("replay-link").hidden = !report.counterexample;
for (const id of ["search", "status-filter", "kind-filter"])
  $(id).addEventListener(id === "search" ? "input" : "change", renderCases);
$("all-edges").addEventListener("change", renderGraph);
$("reset-focus").addEventListener("click", () => {
  focus = null;
  $("graph-note").textContent =
    "Select a state to filter cases. Select a connection to inspect its journeys.";
  renderCases();
  renderGraph();
});
renderCases();
renderGraph();
renderDetail();

for (const [id, change] of [
  ["zoom-in", 0.5],
  ["zoom-out", -0.5],
  ["zoom-fit", 0],
]) {
  $(id).addEventListener("click", () => {
    zoom = change === 0 ? 1 : Math.max(1, Math.min(3, zoom + change));
    renderGraph();
    if (zoom === 1) $("graph-viewport").scrollTo(0, 0);
  });
}
