import test from 'node:test';
import assert from 'node:assert/strict';
import { ModelRunner, runFramework } from '../src/engine.mjs';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { validateModel } from '../src/model.mjs';
import { Browser } from '../src/browser.mjs';
import { DecisionService } from '../src/decisions.mjs';

function fixture(change = () => {}) {
  const document = { models: [{
    id: 'unrelated-flow', name: 'Dispatch flow', startElementId: 'draft',
    properties: { test: {
      version: 1, startUrl: '{{baseUrl}}/compose',
      fields: { units: { target: { role: 'spinbutton', name: 'Units' }, constraints: { type: 'integer', required: true, min: 1, max: 3 }, error: 'Use one to three units' } },
      derived: { amount: { multiply: [{ var: 'input.units' }, 9] } },
    } },
    vertices: [
      { id: 'draft', name: 'v_Compose', properties: { test: { description: 'A dispatch form', expectation: 'The draft is editable', match: [{ type: 'visible', target: { role: 'heading', name: 'Compose dispatch' } }] } } },
      { id: 'receipt', name: 'v_Receipt', properties: { test: { description: 'A dispatch receipt', expectation: 'The dispatch was accepted', match: [{ type: 'visible', target: { role: 'heading', name: 'Dispatch accepted' } }] } } },
    ],
    edges: [{ id: 'send', name: 'e_Send', sourceVertexId: 'draft', targetVertexId: 'receipt', properties: { test: {
      input: { units: 2 }, actions: [
        { type: 'fill', target: { role: 'spinbutton', name: 'Units' }, value: { var: 'input.units' } },
        { type: 'click', target: { role: 'button', name: 'Send dispatch' } },
      ], assertions: [{ type: 'text', contains: 'Amount: {{derived.amount}}' }],
    } } }],
  }] };
  change(document.models[0]);
  const compiled = validateModel(document);
  // Unit tests must not wait for transitions that their fixture never schedules.
  compiled.config.checkTimeoutMs = 0;
  return compiled;
}

const draft = '- heading "Compose dispatch" [ref=a1]\n- spinbutton "Units" [ref=a2]: "2"\n- button "Send dispatch" [ref=a3]';
const receipt = '- heading "Dispatch accepted" [ref=b1]\n- paragraph [ref=b2]: Amount: 18';

class FixtureBrowser extends Browser {
  constructor(snapshot = draft) { super(); this.page = snapshot; this.calls = []; }
  async call(name, args = {}) {
    this.calls.push({ name, args });
    if (name === 'browser_snapshot') return { text: this.page };
    if (name === 'browser_click' && this.nextPage !== undefined) this.page = this.nextPage;
    return { text: '' };
  }
}

function setup({ compiled = fixture(), browser = new FixtureBrowser(), decisions = new DecisionService({ mode: 'offline' }) } = {}) {
  const runner = new ModelRunner({ compiled, browser, decisions, baseUrl: 'https://arbitrary.example', seed: 42, cases: 2, checkTimeoutMs: 0 });
  return { runner, compiled, browser, decisions };
}

function provider({ state = 'draft', confidence = 0.98, passed = 0.99, throws } = {}) {
  return new DecisionService({ apiKey: 'fixture-key', fetchImpl: async () => {
    if (throws) throw throws;
    return { ok: true, json: async () => ({ answers: [
      { name: 'state', type: 'choice', choice: state, confidence, probabilities: [{ value: state, probability: confidence }] },
      { name: 'passed', type: 'predicate', probability: passed },
    ] }) };
  } });
}

test('an arbitrary validated model identifies its states without application bindings', async () => {
  const { runner, browser } = setup();
  assert.equal(runner.startUrl, 'https://arbitrary.example/compose');
  assert.equal((await runner.check('draft')).status, 'PASS');
  browser.page = receipt;
  assert.equal((await runner.check('receipt')).status, 'PASS');
});

test('a deterministic assertion failure cannot be overruled by a remote pass', async () => {
  const compiled = fixture((model) => model.vertices[0].properties.test.assertions = [{ type: 'text', contains: 'Required legal notice' }]);
  const decisions = provider();
  const { runner } = setup({ compiled, decisions });
  const result = await runner.check('draft');
  assert.equal(result.status, 'FAIL');
  assert.equal(result.source, 'deterministic');
  assert.equal(decisions.stats.calls, 0);
});

test('a confidently observed different state fails rather than receiving path coverage', async () => {
  const { runner } = setup({ browser: new FixtureBrowser(receipt) });
  const result = await runner.check('draft');
  assert.equal(result.status, 'FAIL');
  assert.equal(result.observedState, 'receipt');
  assert(result.checks.some((check) => !check.passed));
});

test('offline state recognition remains inconclusive when several model states match', async () => {
  const compiled = fixture((model) => model.vertices[1].properties.test.match = structuredClone(model.vertices[0].properties.test.match));
  const { runner } = setup({ compiled });
  const result = await runner.check('draft');
  assert.equal(result.status, 'INCONCLUSIVE');
  assert.equal(result.passed, false);
});

test('semantic-only states cannot silently pass without a configured provider', async () => {
  const compiled = fixture((model) => model.vertices.forEach((state) => state.properties.test.match = []));
  const { runner } = setup({ compiled });
  assert.equal((await runner.check('draft')).status, 'INCONCLUSIVE');
});

test('uncertain semantic judgment stays inconclusive despite passing deterministic checks', async () => {
  const { runner, decisions } = setup({ decisions: provider({ confidence: 0.6, passed: 0.6 }) });
  const result = await runner.check('draft');
  assert.equal(result.status, 'INCONCLUSIVE');
  assert.equal(result.passed, false);
  assert.equal(decisions.stats.calls, 1);
});

test('semantic state disagreement and a confidently false predicate fail', async () => {
  for (const decisions of [provider({ state: 'receipt' }), provider({ passed: 0.01 })]) {
    const { runner } = setup({ decisions });
    assert.equal((await runner.check('draft')).status, 'FAIL');
  }
});

test('provider outage propagates an inconclusive reason instead of a website failure', async () => {
  const { runner } = setup({ decisions: provider({ throws: new Error('fixture network outage') }) });
  await assert.rejects(runner.check('draft'), { code: 'INCONCLUSIVE', reason: 'NETWORK_ERROR' });
});

test('model action data, expressions and edge postconditions execute together', async () => {
  const { runner, browser, compiled } = setup();
  browser.nextPage = receipt;
  await runner.executeEdge(compiled.edges.get('send'));
  assert.deepEqual(runner.context.input, { units: 2 });
  assert.deepEqual(runner.context.derived, { amount: 18 });
  assert.deepEqual(browser.calls.filter((call) => call.name !== 'browser_snapshot'), [
    { name: 'browser_type', args: { target: 'a2', element: 'spinbutton Units', text: '2' } },
    { name: 'browser_click', args: { target: 'a3', element: 'button Send dispatch' } },
  ]);
  assert.equal((await runner.check('receipt')).status, 'PASS');
});

test('a violated edge postcondition is a test failure even if the click succeeded', async () => {
  const { runner, browser, compiled } = setup();
  browser.nextPage = receipt.replace('18', '999');
  await assert.rejects(runner.executeEdge(compiled.edges.get('send')), { code: 'TEST_FAILURE' });
});

test('offline ambiguous and missing action targets stop before any click', async () => {
  for (const page of [draft + '\n- button "Send dispatch" [ref=a4]', draft.replace('Send dispatch', 'Another action')]) {
    const { runner, browser, compiled } = setup({ browser: new FixtureBrowser(page) });
    await assert.rejects(runner.executeEdge(compiled.edges.get('send')), { code: 'INCONCLUSIVE' });
    assert.equal(browser.calls.some((call) => call.name === 'browser_click'), false);
  }
});

test('a semantic action target never guesses when the provider is offline', async () => {
  const compiled = fixture((model) => model.edges[0].properties.test.actions[1].target = { role: 'button', intent: 'Submit this dispatch' });
  const { runner, browser } = setup({ compiled });
  await assert.rejects(runner.executeEdge(compiled.edges.get('send')), { code: 'INCONCLUSIVE', reason: 'OFFLINE' });
  assert.equal(browser.calls.some((call) => call.name === 'browser_click'), false);
});

test('checkpoint polling allows asynchronous transitions before one semantic judgment', async () => {
  const decisions = provider({ state: 'receipt' });
  const { runner, browser } = setup({ decisions });
  runner.checkTimeoutMs = 500;
  const observe = browser.observe.bind(browser);
  let observations = 0;
  browser.observe = async () => {
    if (++observations === 2) browser.page = receipt;
    return observe();
  };
  assert.equal((await runner.check('receipt')).status, 'PASS');
  assert.equal(observations, 2);
  assert.equal(decisions.stats.calls, 1);
});

test('edge postconditions poll without repeating the browser action', async () => {
  const { runner, browser, compiled } = setup();
  runner.checkTimeoutMs = 500;
  const observe = browser.observe.bind(browser);
  let afterClickObservations = 0;
  browser.observe = async () => {
    if (browser.calls.some((call) => call.name === 'browser_click') && ++afterClickObservations === 2) browser.page = receipt;
    return observe();
  };
  await runner.executeEdge(compiled.edges.get('send'));
  assert.equal(afterClickObservations, 2);
  assert.equal(browser.calls.filter((call) => call.name === 'browser_click').length, 1);
});

test('state expectations resolve current input before reaching the provider', async () => {
  const compiled = fixture((model) => model.vertices[0].properties.test.expectation = 'A dispatch form with {{input.units}} units');
  let observedRequest;
  const decisions = { mode: 'openai', judge: async (request) => { observedRequest = request; return { status: 'PASS' }; } };
  const { runner } = setup({ compiled, decisions });
  runner.setInput({ units: 2 });
  assert.equal((await runner.check('draft')).status, 'PASS');
  assert.equal(observedRequest.expectation, 'A dispatch form with 2 units');
});

test('derived expectations do not require fields from unrelated forms', () => {
  const compiled = fixture((model) => {
    model.properties.test.fields.message = { target: { role: 'textbox', name: 'Message' }, constraints: { type: 'string', required: true }, error: 'Message is required' };
    model.properties.test.derived.messageLabel = { trim: { var: 'input.message' } };
  });
  const { runner } = setup({ compiled });
  runner.setInput({ units: 2 });
  assert.deepEqual(runner.context.derived, { amount: 18 });
  runner.setInput({ message: '  Hello  ' });
  assert.deepEqual(runner.context.derived, { messageLabel: 'Hello' });
});

async function runFixture({ compiled = fixture(), captureError = false, cleanupError = false, fail = false } = {}) {
  const directory = await mkdtemp(join(tmpdir(), 'model-test-engine-'));
  const previousBinary = process.env.GRAPHWALKER_BIN;
  try {
    const modelPath = join(directory, 'model.json');
    await writeFile(modelPath, JSON.stringify(compiled.document));
    const path = ['v_Compose', 'e_Send', 'v_Receipt'].map((name) => JSON.stringify({ currentElementName: name })).join('\n');
    const binary = join(directory, 'graphwalker-fixture');
    await writeFile(binary, `#!/usr/bin/env node\nprocess.stdout.write(${JSON.stringify(path)});\n`, { mode: 0o755 });
    process.env.GRAPHWALKER_BIN = binary;
    const browser = new FixtureBrowser(fail ? receipt : draft);
    browser.start = async () => { browser.client = {}; };
    browser.navigate = async () => {};
    browser.nextPage = receipt;
    browser.screenshot = async () => { if (captureError) throw new Error('Screenshot storage unavailable'); return join(directory, 'fixture.png'); };
    browser.close = async () => { if (cleanupError) throw new Error('Browser cleanup unavailable'); };
    return await runFramework({ modelPath, baseUrl: 'https://arbitrary.example', outputDir: directory,
      browser, provider: 'offline', log: () => {},
    });
  } finally {
    if (previousBinary === undefined) delete process.env.GRAPHWALKER_BIN;
    else process.env.GRAPHWALKER_BIN = previousBinary;
    await rm(directory, { recursive: true, force: true });
  }
}

test('a proven application failure survives screenshot and browser cleanup errors', async () => {
  const report = await runFixture({ fail: true, captureError: true, cleanupError: true });
  assert.equal(report.status, 'FAIL');
  assert.equal(report.reason, 'TEST_FAILURE');
  assert.equal(report.coverage.edges.visited, 0);
  assert.match(report.captureError, /Screenshot/);
  assert.match(report.cleanupError, /cleanup/);
});

test('coverage requirements compare actual fractions before display rounding', async () => {
  const compiled = fixture((model) => {
    model.properties.test.coverage = { edges: 33.333, vertices: 66.666 };
    model.vertices.push({ id: 'unused', name: 'v_Unused', properties: { test: { description: 'An unused screen', expectation: 'Unused screen', match: [{ type: 'visible', target: { role: 'heading', name: 'Unused' } }] } } });
    for (const [id, source, target] of [['unusedA', 'receipt', 'unused'], ['unusedB', 'unused', 'draft']]) {
      model.edges.push({ id, name: id, sourceVertexId: source, targetVertexId: target, properties: { test: { actions: [{ type: 'navigate', url: '{{baseUrl}}/compose' }] } } });
    }
  });
  const report = await runFixture({ compiled });
  assert.equal(report.status, 'PASS');
  assert.equal(report.coverage.edges.percent, 33.33);
  assert.equal(report.coverage.edges.required, 33.333);
});
