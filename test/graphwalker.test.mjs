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
