import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { loadModel } from './model.mjs';
import { Browser } from './browser.mjs';
import { DecisionService } from './decisions.mjs';
import { generatePath } from './graphwalker.mjs';
import { resolveValue, computeDerived, evaluateAssertions, classifyState, validateInput } from './declarative.mjs';
import { testPropertySuite } from './properties.mjs';
import { writeReport } from './report.mjs';

const requirePass = (result, message) => {
  if (result.status === 'PASS') return;
  const error = new Error(`${message}: ${result.status}. ${result.checks?.filter((check) => !check.passed).map((check) => check.description).join('; ') || ''}`);
  error.code = result.status === 'FAIL' ? 'TEST_FAILURE' : 'INCONCLUSIVE';
  error.result = result;
  throw error;
};
const percentage = (visited, total) => total ? Math.round(visited / total * 10000) / 100 : 100;

/** One generic execution engine. The application contract lives entirely in JSON. */
export class ModelRunner {
  constructor({ compiled, browser, decisions, baseUrl, startQuery, seed, cases, onCase = () => {}, onResolution = () => {} }) {
    Object.assign(this, { compiled, browser, decisions, baseUrl, seed, cases, onCase, onResolution });
    this.context = { baseUrl, input: {}, derived: {}, graph: { raw: '' } };
    this.startUrl = resolveValue(compiled.config.startUrl, this.context);
    const url = new URL(this.startUrl);
    if (!['http:', 'https:'].includes(url.protocol)) throw new Error('The model startUrl must resolve to an HTTP(S) URL.');
    if (startQuery) for (const [key, value] of new URLSearchParams(startQuery)) url.searchParams.set(key, value);
    this.startUrl = url.href;
    this.completedProperties = new Set();
    this.checkTimeoutMs = compiled.config.checkTimeoutMs ?? 2000;
    this.stateDescriptions = compiled.model.vertices.map((state) => ({ id: state.id, description: state.properties.test.description }));
    this.stateMatches = compiled.model.vertices.map((state) => ({ id: state.id, match: state.properties.test.match || [] }));
  }

  setInput(input) {
    this.context.input = { ...input };
    this.context.derived = {};
    // Each expression depends only on its own fields; independent forms need not
    // supply every other field declared in the graph.
    this.context.derived = computeDerived(this.compiled.config.derived, this.context, this.compiled.fields);
  }

  async actions(actions = []) {
    for (const action of actions) {
      await this.browser.perform(resolveValue(action, this.context), {
        context: this.context, decisions: this.decisions, onResolution: this.onResolution,
      });
    }
  }

  async observeUntil(evaluate) {
    const deadline = Date.now() + this.checkTimeoutMs;
    for (;;) {
      const evidence = await this.browser.observe();
      const result = evaluate(evidence);
      if (result.ready || Date.now() >= deadline) return { ...result, evidence };
      await delay(Math.min(100, Math.max(0, deadline - Date.now())));
    }
  }

  async check(stateId, { semantic = true, extraAssertions = [] } = {}) {
    const state = this.compiled.states.get(stateId);
    if (!state) throw new Error(`Unknown state ${stateId}.`);
    const contract = state.properties.test;
    const { evidence, observedState, checks } = await this.observeUntil((observation) => {
      const observedState = classifyState(this.stateMatches, observation, this.context);
      const checks = evaluateAssertions([...(contract.match || []), ...(contract.assertions || []), ...extraAssertions], observation, this.context);
      if (observedState !== '__unknown__') checks.push({ description: `Observed state is ${stateId} (actual ${observedState})`, passed: observedState === stateId });
      const ready = checks.every((check) => check.passed) && (observedState === stateId || (semantic && this.decisions.mode !== 'offline'));
      return { observedState, checks, ready };
    });
    if (checks.some((check) => !check.passed)) return { status: 'FAIL', passed: false, observedState, checks, evidence, source: 'deterministic' };
    if (semantic && this.decisions.mode !== 'offline') {
      const decision = await this.decisions.judge({
        states: this.stateDescriptions, expectedState: stateId,
        expectation: resolveValue(contract.expectation, this.context),
        evidence: { snapshot: evidence.snapshot },
      });
      return { ...decision, passed: decision.status === 'PASS', checks, evidence };
    }
    const status = observedState === stateId && checks.length ? 'PASS' : 'INCONCLUSIVE';
    return { status, passed: status === 'PASS', observedState, checks, evidence, source: 'deterministic' };
  }

  async executeEdge(edge, { allowProperty = true } = {}) {
    const contract = edge.properties.test;
    if (contract.clearInput) this.setInput({});
    if (contract.input) this.setInput(resolveValue(contract.input, this.context));
    await this.actions(contract.actions);
    if (contract.property) {
      if (!allowProperty) throw new Error('A reset path cannot recursively execute a property suite.');
      if (!this.completedProperties.has(contract.property)) {
        const name = contract.property;
        const suite = this.compiled.suites[name];
        await testPropertySuite({ name, suite, fields: this.compiled.fields, seed: this.seed, cases: this.cases,
          onCase: this.onCase, executeCase: (input, expectedState, validity) => this.propertyCase(suite, input, expectedState, validity),
        });
        this.completedProperties.add(name);
        await this.resetFor(suite);
      }
    }
    if (contract.assertions?.length) {
      const { checks } = await this.observeUntil((evidence) => {
        const checks = evaluateAssertions(contract.assertions, evidence, this.context);
        return { checks, ready: checks.every((check) => check.passed) };
      });
      requirePass({ status: checks.every((check) => check.passed) ? 'PASS' : 'FAIL', checks }, `Postconditions on ${edge.name}`);
    }
  }

  async resetFor(suite) {
    this.context = { baseUrl: this.baseUrl, input: {}, derived: {}, graph: { raw: '' } };
    await this.browser.navigate(this.startUrl);
    requirePass(await this.check(this.compiled.model.startElementId, { semantic: false }), 'Property reset initial state');
    for (const id of suite.resetEdges) {
      const edge = this.compiled.edges.get(id);
      await this.executeEdge(edge, { allowProperty: false });
      requirePass(await this.check(edge.targetVertexId, { semantic: false }), `Property reset ${edge.name}`);
    }
  }

  async propertyCase(suite, input, expectedState, validity) {
    await this.resetFor(suite);
    this.setInput(input);
    for (const field of suite.fields) {
      await this.actions([{ type: 'fill', target: this.compiled.fields[field].target, value: { var: `input.${field}` } }]);
    }
    await this.actions(suite.submit);
    const extraAssertions = [...new Set(validity.violations.map((violation) => violation.field))]
      .map((field) => ({ type: 'text', contains: this.compiled.fields[field].error }));
    // Hegel reduces against deterministic model requirements. Remote probabilistic
    // verdicts are reserved for graph checkpoints, keeping reductions reproducible.
    return this.check(expectedState, { semantic: false, extraAssertions });
  }
}

export async function runFramework({ modelPath, baseUrl = '', startQuery, provider = 'openai', seed = 42, cases, maxSteps = 200,
  maxCalls = 80, threshold = 0.85, headed = false, outputDir = 'artifacts', replayPath, log = console.log,
  browser: suppliedBrowser, decisions: suppliedDecisions } = {}) {
  if (!modelPath) throw new Error('Supply --model path/to/model.json.');
  for (const [key, value, max] of [['seed', seed, 2147483647], ['maxSteps', maxSteps, 10000], ['maxCalls', maxCalls, 10000]]) {
    if (!Number.isInteger(value) || value < 1 || value > max) throw new Error(`${key} must be an integer from 1 to ${max}.`);
  }
  if (cases !== undefined && (!Number.isInteger(cases) || cases < 1 || cases > 200)) throw new Error('cases must be an integer from 1 to 200.');
  const compiled = await loadModel(modelPath);
  const modelHash = createHash('sha256').update(JSON.stringify(compiled.document)).digest('hex');
  const directory = resolve(outputDir, `${new Date().toISOString().replace(/[:.]/g, '-')}-${provider}`);
  await mkdir(directory, { recursive: true });
  const decisions = suppliedDecisions || new DecisionService({ mode: provider, maxCalls, threshold,
    apiKey: process.env.DECISION_API_KEY || process.env.OPENAI_API_KEY,
    model: process.env.DECISION_MODEL || process.env.OPENAI_DECISION_MODEL || 'gpt-6-luna',
    endpoint: process.env.DECISION_ENDPOINT || 'https://api.openai.com/v1/decisions',
  });
  const browser = suppliedBrowser || new Browser({ outputDir: resolve(directory, 'browser'), headed });
  const report = { status: 'RUNNING', model: compiled.model.name, modelPath: resolve(modelPath), modelHash, provider, seed,
    startedAt: new Date().toISOString(), steps: [], formCases: [], resolutions: [], replay: Boolean(replayPath), directory };
  const visitedEdges = new Set(), visitedVertices = new Set(), visitedRequirements = new Set(), screenshots = new Set();
  let currentStep, runner;
  const credit = (element) => {
    (element.type === 'edge' ? visitedEdges : visitedVertices).add(element.id);
    for (const requirement of element.requirements || []) visitedRequirements.add(requirement);
  };
  try {
    runner = new ModelRunner({ compiled, browser, decisions, baseUrl, startQuery, seed, cases,
      onResolution: (entry) => report.resolutions.push(entry),
      onCase: (entry) => { report.formCases.push(entry); log(`  ${entry.status} ${entry.source} ${JSON.stringify(entry.input)}`); },
    });
    report.startUrl = runner.startUrl;
    await writeFile(resolve(directory, 'model.json'), JSON.stringify(compiled.document, null, 2));
    const replay = replayPath ? JSON.parse(await readFile(replayPath, 'utf8')) : null;
    if (replay && replay.modelHash !== modelHash) throw new Error('Replay model hash differs. Use the saved model.json from the original report.');
    const path = replay ? [] : await generatePath({ modelPath, seed, maxSteps, requiredEdgeCoverage: compiled.coverage.edges });
    report.plannedPath = path;
    log(`${compiled.model.name}: ${provider} · ${path.length} planned elements · seed ${seed}`);
    await browser.start();
    if (replay) {
      const suite = compiled.suites[replay.suite];
      if (!suite) throw new Error(`Unknown replay property ${replay.suite}.`);
      const fields = Object.fromEntries(suite.fields.map((key) => [key, compiled.fields[key]]));
      const validity = validateInput(fields, replay.input);
      const result = await runner.propertyCase(suite, replay.input, validity.valid ? suite.validState : suite.invalidState, validity);
      report.formCases.push({ ...result, judgmentSource: result.source, suite: replay.suite, input: replay.input, source: 'replay' });
      requirePass(result, 'Replayed property case');
    } else {
      await browser.navigate(runner.startUrl);
      let pendingEdge;
      for (const element of path) {
        currentStep = { id: element.id, name: element.name, type: element.type, status: 'RUNNING' };
        report.steps.push(currentStep);
        if (element.graphData !== undefined) runner.context.graph = { raw: element.graphData };
        if (element.type === 'edge') {
          await runner.executeEdge(element);
          pendingEdge = element;
        } else {
          const result = await runner.check(element.id);
          Object.assign(currentStep, result);
          if (!screenshots.has(element.id) || result.status !== 'PASS') {
            try {
              currentStep.screenshot = await browser.screenshot(`${report.steps.length}-${element.id.replace(/[^A-Za-z0-9_-]/g, '_')}.png`);
              screenshots.add(element.id);
            } catch (error) {
              currentStep.captureError = error.message;
              report.captureError = error.message;
            }
          }
          requirePass(result, `Checkpoint ${element.name}`);
          credit(element);
          if (pendingEdge) { credit(pendingEdge); pendingEdge = null; }
        }
        currentStep.status = 'PASS';
        log(`PASS ${element.name}`);
      }
      const missingSuites = Object.keys(compiled.suites).filter((name) => !runner.completedProperties.has(name));
      if (missingSuites.length) throw new Error(`Incomplete property coverage: ${missingSuites.join(', ')}.`);
      if (visitedVertices.size / compiled.states.size * 100 < compiled.coverage.vertices) throw new Error('Required vertex coverage was not achieved.');
      if (visitedEdges.size / compiled.edges.size * 100 < compiled.coverage.edges) throw new Error('Required verified edge coverage was not achieved.');
      if ((compiled.coverage.requirements || []).some((id) => !visitedRequirements.has(id))) throw new Error('Required requirement coverage was not achieved.');
    }
    report.status = 'PASS';
  } catch (error) {
    report.status = error.code === 'TEST_FAILURE' ? 'FAIL' : 'INCONCLUSIVE';
    report.error = error.message;
    report.reason = error.reason || error.code || 'EXECUTION_ERROR';
    if (currentStep) { currentStep.status = report.status; currentStep.error = error.message; }
    if (error.failingCase) {
      report.counterexample = error.failingCase;
      await writeFile(resolve(directory, 'replay.json'), JSON.stringify({ modelHash, suite: error.failingCase.suite, input: error.failingCase.input, seed }, null, 2));
    }
    try { if (browser.client) { report.failureEvidence = await browser.observe(); report.failureScreenshot = await browser.screenshot('failure.png'); } }
    catch (captureError) { report.captureError = captureError.message; }
    log(`${report.status}: ${error.message}`);
  } finally {
    try { await browser.close(); } catch (error) {
      report.cleanupError = error.message;
      if (report.status !== 'FAIL') report.status = 'INCONCLUSIVE';
    }
    const allRequirements = [...new Set([...compiled.model.vertices, ...compiled.model.edges].flatMap((item) => item.requirements || []))];
    report.coverage = {
      edges: { visited: visitedEdges.size, total: compiled.edges.size, percent: percentage(visitedEdges.size, compiled.edges.size), required: compiled.coverage.edges, ids: [...visitedEdges] },
      vertices: { visited: visitedVertices.size, total: compiled.states.size, percent: percentage(visitedVertices.size, compiled.states.size), required: compiled.coverage.vertices, ids: [...visitedVertices] },
      requirements: { visited: visitedRequirements.size, total: allRequirements.length, ids: [...visitedRequirements], missing: allRequirements.filter((id) => !visitedRequirements.has(id)) },
      properties: { completed: [...(runner?.completedProperties || [])], total: Object.keys(compiled.suites).length },
    };
    report.decisions = { ...decisions.stats };
    report.finishedAt = new Date().toISOString();
    await writeReport(directory, report);
    log(`${report.status} · verified edges ${visitedEdges.size}/${compiled.edges.size} · input attempts ${report.formCases.length} · API calls ${decisions.stats.calls}`);
    log(`Report: ${resolve(directory, 'report.html')}`);
  }
  return report;
}
