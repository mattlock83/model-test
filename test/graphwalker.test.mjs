import test from 'node:test';
import assert from 'node:assert/strict';
import { parsePath, generatePath } from '../src/graphwalker.mjs';

const model = () => ({ models: [{
  startElementId: 'home',
  vertices: [{ id: 'home', name: 'v_Home' }, { id: 'form', name: 'v_Form' }],
  edges: [
    { id: 'open', name: 'e_Open', sourceVertexId: 'home', targetVertexId: 'form' },
    { id: 'back', name: 'e_Back', sourceVertexId: 'form', targetVertexId: 'home' },
  ],
}] });
const output = (...names) => names.map((currentElementName) => JSON.stringify({ currentElementName })).join('\n');
const complete = output('v_Home', 'e_Open', 'v_Form', 'e_Back', 'v_Home');

test('GraphWalker JSONL preserves source model identity and typed steps', () => {
  const path = parsePath(model(), complete);
  assert.deepEqual(path.map(({ id, type }) => ({ id, type })), [
    { id: 'home', type: 'vertex' }, { id: 'open', type: 'edge' },
    { id: 'form', type: 'vertex' }, { id: 'back', type: 'edge' }, { id: 'home', type: 'vertex' },
  ]);
});

test('validates the actual Rust CLI currentElementId field against the seed model', () => {
  const document = model();
  document.models[0].id = 'booking';
  const elements = [...document.models[0].vertices, ...document.models[0].edges];
  const records = complete.split('\n').map((line) => {
    const { currentElementName } = JSON.parse(line);
    const element = elements.find((candidate) => candidate.name === currentElementName);
    return { modelId: 'booking', modelName: 'Booking', currentElementName, currentElementId: element.id };
  });
  const encoded = () => records.map((record) => JSON.stringify(record)).join('\n');
  assert.equal(parsePath(document, encoded()).length, 5);
  records[1].currentElementId = 'wrong';
  assert.throws(() => parsePath(document, encoded()), /name\/id mismatch/);
  records[1].currentElementId = 'open';
  records[1].modelId = 'wrong-model';
  assert.throws(() => parsePath(document, encoded()), /unexpected model id/);
});

test('a final emitted edge gets a destination verification checkpoint', () => {
  const path = parsePath(model(), output('v_Home', 'e_Open', 'v_Form', 'e_Back'));
  assert.equal(path.at(-1).id, 'home');
  assert.equal(path.at(-1).completionCheckpoint, true);
});

test('coverage and the step cap cannot silently pass on truncated paths', () => {
  assert.throws(() => parsePath(model(), output('v_Home', 'e_Open', 'v_Form')), /incomplete edge coverage/);
  assert.throws(() => parsePath(model(), complete, { maxSteps: 4 }), /exceeds maxSteps/);
  assert.throws(() => parsePath(model(), output('v_Home', 'e_Open', 'v_Form', 'e_Back'), { maxSteps: 4 }), /final vertex checkpoint beyond maxSteps/);
});

test('rejects ambiguous identities, unrecognized output, and disconnected paths', () => {
  const duplicate = model();
  duplicate.models[0].vertices[1].name = 'v_Home';
  assert.throws(() => parsePath(duplicate, complete), /Duplicate model element name/);
  assert.throws(() => parsePath(model(), output('v_Unknown')), /unknown element/);
  assert.throws(() => parsePath(model(), 'not JSON'), /Invalid GraphWalker JSON/);
  assert.throws(() => parsePath(model(), 'null'), /Invalid GraphWalker output record/);
  assert.throws(() => parsePath(model(), output('v_Home', 'e_Back')), /Invalid GraphWalker transition/);
  assert.throws(() => parsePath(model(), output('v_Form', 'e_Back')), /must start/);
});

test('rejects GraphWalker seed zero, which would silently enable random seeding', async () => {
  await assert.rejects(generatePath({ modelPath: 'unused.json', seed: 0 }), /seed must be a positive/);
});

test('explicit edge coverage allows shorter length/vertex/guard-constrained paths', () => {
  const partial = output('v_Home', 'e_Open', 'v_Form');
  for (const generator of ['random(length(3))', 'random(vertex_coverage(100))', 'quick_random(edge_coverage(50))']) {
    const document = model();
    document.models[0].generator = generator;
    document.models[0].edges[1].guard = 'enabled';
    assert.equal(parsePath(document, partial, { requiredEdgeCoverage: 50 }).length, 3);
    assert.throws(() => parsePath(document, partial, { requiredEdgeCoverage: 50.1 }), /50\.00% < required 50\.1%/);
  }
  assert.equal(parsePath(model(), output('v_Home'), { requiredEdgeCoverage: 0 }).length, 1);
  assert.throws(() => parsePath(model(), output('v_Home', 'e_Open'), { requiredEdgeCoverage: 50, maxSteps: 2 }), /final vertex checkpoint/);
});

test('coverage thresholds must be finite numbers within zero through one hundred', async () => {
  for (const requiredEdgeCoverage of [-1, 101, NaN, Infinity, '50', null]) {
    assert.throws(() => parsePath(model(), complete, { requiredEdgeCoverage }), /requiredEdgeCoverage/);
    await assert.rejects(generatePath({ modelPath: 'unused.json', requiredEdgeCoverage }), /requiredEdgeCoverage/);
  }
});

test('preserves model requirements, guards, actions and declarative metadata without evaluating them', () => {
  const document = model();
  Object.assign(document.models[0].edges[0], {
    requirements: ['OPEN-1', 'OPEN-2'],
    guard: 'enabled',
    actions: ['visits += 1;'],
    properties: { test: { actions: [{ type: 'click', target: { name: 'Open', role: 'button' } }] } },
  });
  const step = parsePath(document, complete)[1];
  assert.deepEqual(step.requirements, ['OPEN-1', 'OPEN-2']);
  assert.equal(step.guard, 'enabled');
  assert.deepEqual(step.actions, ['visits += 1;']);
  assert.deepEqual(step.properties, document.models[0].edges[0].properties);
});

test('verbose CLI data is preserved as opaque evidence, including values that look like code', () => {
  const records = complete.split('\n').map((line, i) => ({ ...JSON.parse(line), data: i === 1 ? 'enabled=true;visits=1;label=a;b=process.exit()' : '' }));
  const path = parsePath(model(), records.map((record) => JSON.stringify(record)).join('\n'));
  assert.equal(path[0].graphData, '');
  assert.equal(path[1].graphData, 'enabled=true;visits=1;label=a;b=process.exit()');
  records[0].data = { enabled: true, visits: 1 };
  assert.deepEqual(parsePath(model(), records.map((record) => JSON.stringify(record)).join('\n'))[0].graphData, { enabled: true, visits: 1 });
  for (const invalid of [null, [], 1, true]) {
    records[0].data = invalid;
    assert.throws(() => parsePath(model(), records.map((record) => JSON.stringify(record)).join('\n')), /Invalid GraphWalker data/);
  }
});

test('a synthetic destination checkpoint does not invent execution of native vertex actions', () => {
  const records = output('v_Home', 'e_Open', 'v_Form', 'e_Back').split('\n').map((line) => ({ ...JSON.parse(line), data: 'count=1' }));
  const path = parsePath(model(), records.map((record) => JSON.stringify(record)).join('\n'));
  assert.equal(path.at(-2).graphData, 'count=1');
  assert.equal(path.at(-1).graphData, undefined);
  assert.equal(path.at(-1).completionCheckpoint, true);
});
