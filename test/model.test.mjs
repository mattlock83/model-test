import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadModel, validateModel } from '../src/model.mjs';

const booking = JSON.parse(await readFile(new URL('../models/booking.json', import.meta.url), 'utf8'));
const fresh = () => structuredClone(booking);
const config = (document) => document.models[0].properties.test;
const suite = (document) => Object.values(config(document).properties)[0];
const stateTest = (document) => document.models[0].vertices[0].properties.test;
const edgeTest = (document) => document.models[0].edges[0].properties.test;
function rejects(change, pattern = /Invalid test model:/) {
  const document = fresh();
  change(document);
  assert.throws(() => validateModel(document), pattern);
}

test('both independent demo models load, preserving native GraphWalker features', async () => {
  const first = await loadModel(new URL('../models/booking.json', import.meta.url));
  const second = await loadModel(new URL('../models/feedback.json', import.meta.url));
  assert.equal(first.edges.size, 10);
  assert.equal(second.edges.size, 5);
  assert.equal(first.model.edges.find((edge) => edge.id === 'e_Confirm').guard, 'hasBooking');
  assert.deepEqual(first.model.actions, ['hasBooking = false;']);
  assert.deepEqual(first.coverage, { edges: 100, vertices: 100 });
});

test('missing graphs, duplicate IDs/names and broken edges are rejected', () => {
  for (const document of [null, {}, { models: [] }, { models: [null] }]) assert.throws(() => validateModel(document), /Invalid test model:/);
  rejects((d) => d.models.push(d.models[0]), /exactly one/);
  rejects((d) => d.models[0].vertices[1].id = d.models[0].vertices[0].id, /unique/);
  rejects((d) => d.models[0].edges[0].name = d.models[0].vertices[0].name, /unique/);
  rejects((d) => d.models[0].startElementId = 'absent', /startElementId/);
  rejects((d) => d.models[0].edges[0].targetVertexId = 'absent', /unknown vertex/);
});

test('unknown test metadata fails before execution instead of ignoring misspellings', () => {
  for (const change of [
    (d) => config(d).dervied = {},
    (d) => config(d).demo.script = 'doSomething()',
    (d) => stateTest(d).assertons = [],
    (d) => edgeTest(d).before = 'doSomething()',
    (d) => config(d).fields.name.gnerator = {},
    (d) => suite(d).boundry = true,
    (d) => config(d).coverage.edge = 100,
  ]) rejects(change, /unsupported field/);
});

test('actions require their supported fields and reject code, selectors and ignored options', () => {
  const bad = [
    { type: 'evaluate', code: 'document.body' },
    { type: 'click', target: { selector: '#submit' } },
    { type: 'click', target: { role: 'button' } },
    { type: 'click', target: { name: 'Submit' }, value: 'ignored' },
    { type: 'fill', target: { name: 'Name' } },
    { type: 'press', key: '' },
    { type: 'check', target: { name: 'Consent' }, value: 'false' },
    { type: 'navigate', url: 'javascript:alert(1)' },
    { type: 'navigate', url: 'file:///tmp/data' },
  ];
  for (const action of bad) rejects((d) => edgeTest(d).actions = [action]);
  rejects((d) => edgeTest(d).actions = [], /1–200/);
  const document = fresh();
  edgeTest(document).actions = [
    { type: 'click', target: { intent: 'Open the form' } },
    { type: 'press', key: 'Escape' },
    { type: 'select', target: { name: 'Options' }, value: ['First', 'Second'] },
    { type: 'check', target: { name: 'Consent' }, value: false },
    { type: 'navigate', url: '{{baseUrl}}/form' },
  ];
  assert.doesNotThrow(() => validateModel(document));
});

test('deterministic assertion targets require roles and never accept semantic-only selectors', () => {
  for (const assertion of [
    { type: 'visible', target: { name: 'Example' } },
    { type: 'visible', target: { intent: 'The expected heading' } },
    { type: 'visible', target: { role: 'heading', intent: 'Ignored intent' } },
    { type: 'javascript', value: true },
    { type: 'text', contains: '' },
    { type: 'text', contains: 'Okay', equals: 'Ignored' },
    { type: 'value', target: { role: 'textbox', name: 'Name' } },
    { type: 'value', target: { role: 'textbox', name: 'Name' }, equals: null },
    { type: 'count', target: { role: 'button' }, equals: -1 },
    { type: 'count', target: { role: 'button' }, equals: true },
  ]) rejects((d) => stateTest(d).assertions = [assertion]);
  rejects((d) => edgeTest(d).assertions = [{ type: 'invalid' }], /unsupported assertion/);
});

test('assertion conditions are explicit variable-existence checks', () => {
  rejects((d) => stateTest(d).assertions[0].when = { code: 'true' }, /unsupported field/);
  rejects((d) => stateTest(d).assertions[0].when = {}, /unsafe variable/);
  rejects((d) => stateTest(d).assertions[0].when = { exists: 'input.noSuchField' }, /unknown variable/);
  const document = fresh();
  stateTest(document).assertions[0].when = { exists: 'input.name' };
  assert.doesNotThrow(() => validateModel(document));
});

test('expressions reject unknown operators, malformed templates and undeclared variables', () => {
  for (const value of [
    { execute: 'process.exit()' }, { divide: [2, 1] }, { number: 'nope' },
    { multiply: [] }, { var: 'input.constructor' }, { var: 'input.noSuchField' },
    { var: 'graph.hasBooking' }, '{{missing}}', '{{input.name', '{{derived.absent}}',
  ]) rejects((d) => edgeTest(d).input = { name: value });
  rejects((d) => config(d).derived.first = { var: 'derived.later' }, /unknown variable/);
  const document = fresh();
  config(document).derived.raw = { var: 'graph.raw' };
  config(document).derived.totalWithFee = { add: [{ var: 'derived.total' }, 5] };
  assert.doesNotThrow(() => validateModel(document));
});

test('input constraints validate eagerly and incompatible constraints are rejected', () => {
  for (const constraints of [
    { type: 'integer', min: '1' }, { type: 'integer', min: 2, max: 1 },
    { type: 'integer', minLength: 2 }, { type: 'string', min: 2 },
    { type: 'string', maxLength: 1001 }, { type: 'string', typo: true },
    { type: 'string', pattern: '[' }, { type: 'string', required: 'yes' },
  ]) rejects((d) => config(d).fields.name.constraints = constraints);
  rejects((d) => config(d).fields.constructor = config(d).fields.name, /unsafe key/);
});

test('explicit generators reject unbounded, invalid and unsupported domains', () => {
  for (const generator of [
    { type: 'script', code: 'random()' }, { type: 'integer', min: 0, max: 1000001 },
    { type: 'integer', min: 0.5, max: 1 }, { type: 'number', min: 2, max: 1 },
    { type: 'number', min: 0 }, { type: 'text', maxLength: 1001 },
    { type: 'text', minLength: -1 }, { type: 'sample', values: [] },
    { type: 'sample', values: [{}] }, { type: 'sample', values: ['x'.repeat(1001)] },
    { type: 'oneOf', generators: [] }, { type: 'oneOf', generators: [{ type: 'invalid' }] },
    { type: 'text', maxLength: 8, alphabet: 'ignored' },
  ]) rejects((d) => config(d).fields.name.generator = generator);
  const document = fresh();
  config(document).fields.name.generator = { type: 'oneOf', generators: [
    { type: 'text', minLength: 0, maxLength: 12 }, { type: 'sample', values: ['Ada', ''] },
  ] };
  assert.doesNotThrow(() => validateModel(document));
});

test('property baselines contain exactly the tested fields and satisfy their constraints', () => {
  rejects((d) => delete suite(d).baseline.name, /exactly the suite fields/);
  rejects((d) => suite(d).baseline.unknown = 'ignored', /exactly the suite fields/);
  rejects((d) => suite(d).baseline.name = 'A', /satisfy/);
  rejects((d) => suite(d).baseline.email = '', /satisfy/);
  rejects((d) => suite(d).fields.push('name'), /duplicate/);
  rejects((d) => suite(d).fields.push('unknown'), /unknown/);
  const document = fresh();
  config(document).fields.optional = { target: { name: 'Optional' }, constraints: { type: 'string' }, error: 'Invalid optional value' };
  assert.doesNotThrow(() => validateModel(document), 'fields outside a property scope are not required in its baseline');
});

test('property examples may be invalid but must be bounded complete literal inputs', () => {
  const document = fresh();
  suite(document).examples = [{ name: '', email: 'invalid', seats: 5 }];
  assert.doesNotThrow(() => validateModel(document));
  rejects((d) => suite(d).examples = [{ name: '' }], /exactly the suite fields/);
  rejects((d) => suite(d).examples = [{ name: { var: 'input.name' }, email: 'x', seats: 1 }], /JSON scalar/);
  rejects((d) => suite(d).boundaries = 'true', /boolean/);
  rejects((d) => suite(d).cases = 0, /1–200/);
});

test('property reset paths must connect the start vertex to the property self-loop', () => {
  rejects((d) => suite(d).resetEdges = [], /does not reach/);
  rejects((d) => suite(d).resetEdges = ['e_Confirm'], /connected path/);
  rejects((d) => suite(d).resetEdges = ['e_OpenForm', 'e_ValidateInputs'], /without property suites/);
  rejects((d) => d.models[0].edges[0].guard = 'hasBooking', /guarded edges/);
  rejects((d) => d.models[0].edges.find((edge) => edge.properties.test.property).targetVertexId = 'v_Error', /self-loop/);
  rejects((d) => d.models[0].edges.find((edge) => edge.properties.test.property).properties.test.actions = edgeTest(d).actions, /ignored actions/);
});

test('property outcomes require distinct known states with unconditional match evidence', () => {
  rejects((d) => suite(d).invalidState = suite(d).validState, /distinct/);
  rejects((d) => suite(d).validState = 'absent', /distinct/);
  rejects((d) => d.models[0].vertices.find((vertex) => vertex.id === suite(d).validState).properties.test.match = [], /unconditional/);
  rejects((d) => d.models[0].vertices.find((vertex) => vertex.id === suite(d).validState).properties.test.match.forEach((item) => item.when = { exists: 'input.name' }), /unconditional/);
});

test('coverage contracts use bounded percentages and explicit unique requirement IDs', () => {
  rejects((d) => config(d).coverage.edges = 101, /0–100/);
  rejects((d) => config(d).coverage.vertices = '100', /0–100/);
  rejects((d) => config(d).coverage.requirements = 'Confirm', /requirement IDs/);
  rejects((d) => config(d).coverage.requirements = ['Confirm', 'Confirm'], /requirement IDs/);
  const document = fresh();
  config(document).coverage.requirements = ['Confirm'];
  assert.deepEqual(validateModel(document).coverage.requirements, ['Confirm']);
});


test('checkpoint polling timeout accepts bounded integer milliseconds', () => {
  for (const checkTimeoutMs of [0, 1, 2000, 30000]) {
    const document = fresh();
    config(document).checkTimeoutMs = checkTimeoutMs;
    assert.equal(validateModel(document).config.checkTimeoutMs, checkTimeoutMs);
  }
  for (const checkTimeoutMs of [-1, 30001, 1.5, '2000', null, true]) {
    rejects((document) => config(document).checkTimeoutMs = checkTimeoutMs, /checkTimeoutMs must be an integer/);
  }
  assert.equal(validateModel(fresh()).config.checkTimeoutMs, undefined, 'the engine owns the default timeout');
});


test('state descriptions remain static while expectations accept dynamic input', () => {
  rejects((document) => stateTest(document).description = 'Review for {{input.name}}', /description must be static/);
  const document = fresh();
  stateTest(document).expectation = 'Review for {{input.name}}';
  assert.doesNotThrow(() => validateModel(document));
});
