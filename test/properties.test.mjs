import test from 'node:test';
import assert from 'node:assert/strict';
import * as hegel from '@hegeldev/hegel';
import { boundaryValues, generatorFor, testPropertySuite } from '../src/properties.mjs';

const field = (constraints, generator) => ({ constraints, ...(generator ? { generator } : {}) });
const settings = { seed: 42, database: hegel.Database.disabled, suppressHealthCheck: [hegel.HealthCheck.TooSlow], reportMultipleFailures: false, testCases: 24 };

async function drawValues(definition, baseline, cases = 24) {
  const result = [];
  const generator = generatorFor(definition, baseline);
  await hegel.testAsync(async (tc) => { result.push(tc.draw(generator)); }, { ...settings, testCases: cases });
  return result;
}

function fixture() {
  return {
    name: 'arbitrary-fields', seed: 42,
    fields: {
      widgets: field({ type: 'integer', required: true, min: 1, max: 20 }, { type: 'integer', min: 0, max: 30 }),
      annotation: field({ type: 'string', required: true, minLength: 2, maxLength: 6 }, { type: 'text', minLength: 0, maxLength: 8 }),
    },
    suite: { fields: ['widgets', 'annotation'], baseline: { widgets: 2, annotation: 'valid' }, validState: 'accepted', invalidState: 'rejected', casesPerField: 4, cases: 5, boundaries: true },
  };
}

test('boundary partitions include each requirement boundary and preserve the baseline', () => {
  const numeric = boundaryValues(field({ type: 'integer', min: 1, max: 4 }), 2);
  for (const value of [2, '', 0, 1, 3, 4, 5, '1.5', 'not-a-number']) assert.ok(numeric.includes(value));
  assert.equal(new Set(numeric.map(JSON.stringify)).size, numeric.length);
  const text = boundaryValues(field({ type: 'string', minLength: 2, maxLength: 5 }), 'ok');
  for (const value of ['ok', '', 'x', 'xx', 'xxx', 'xxxx', 'xxxxx', 'xxxxxx', '  ok  ']) assert.ok(text.includes(value));
  const email = boundaryValues(field({ type: 'email' }), 'first@example.test');
  for (const value of ['first@example.test', '', 'user@example.test', 'missing-at', 'user@localhost', 'user @example.test']) assert.ok(email.includes(value));
});

test('actual Hegel integer, floating-point and Unicode text generators honor model bounds', async () => {
  const integers = await drawValues(field({ type: 'integer' }, { type: 'integer', min: -3, max: 6 }), 2);
  assert.ok(integers.length > 1);
  assert.ok(integers.every((value) => Number.isInteger(value) && value >= -3 && value <= 6));
  const numbers = await drawValues(field({ type: 'number' }, { type: 'number', min: -1.5, max: 2.5 }), 1);
  assert.ok(numbers.length > 1);
  assert.ok(numbers.every((value) => Number.isFinite(value) && value >= -1.5 && value <= 2.5));
  const texts = await drawValues(field({ type: 'string' }, { type: 'text', minLength: 2, maxLength: 7 }), 'ok');
  assert.ok(texts.length > 1);
  assert.ok(texts.every((value) => typeof value === 'string' && [...value].length >= 2 && [...value].length <= 7));
});

test('actual Hegel sample and nested oneOf generators use model-owned alternatives', async () => {
  const samples = await drawValues(field({ type: 'string' }, { type: 'sample', values: ['alpha', 'beta', 'gamma'] }), 'alpha');
  assert.deepEqual(new Set(samples), new Set(['alpha', 'beta', 'gamma']));
  const mixed = await drawValues(field({ type: 'string' }, {
    type: 'oneOf', generators: [
      { type: 'sample', values: ['special'] },
      { type: 'oneOf', generators: [{ type: 'integer', min: 0, max: 2 }, { type: 'sample', values: ['other'] }] },
    ],
  }), 'special', 40);
  assert.ok(mixed.includes('special'));
  assert.ok(mixed.includes('other'));
  assert.ok(mixed.some((value) => typeof value === 'number'));
  assert.ok(mixed.every((value) => ['special', 'other'].includes(value) || Number.isInteger(value) && value >= 0 && value <= 2));
});

test('an omitted generator samples model requirement partitions', async () => {
  const definition = field({ type: 'integer', min: 1, max: 4 });
  const expected = boundaryValues(definition, 2);
  const actual = await drawValues(definition, 2, 40);
  assert.ok(new Set(actual).size > 2);
  assert.ok(actual.every((value) => expected.includes(value)));
  // Hegel sampling is not exhaustive; deterministic boundary passes guarantee
  // coverage of every partition separately in testPropertySuite.
});

test('property execution isolates fields before combinations and accounts for all actual cases', async () => {
  const setup = fixture();
  const original = structuredClone(setup.suite.baseline);
  const records = [], executed = [];
  const result = await testPropertySuite({ ...setup,
    executeCase: async (input, expectedState, validity) => {
      executed.push(structuredClone(input));
      // Independent expectation for this fixture, not the framework validator.
      const valid = Number.isInteger(input.widgets) && input.widgets >= 1 && input.widgets <= 20
        && typeof input.annotation === 'string' && [...input.annotation].length >= 2 && [...input.annotation].length <= 6;
      assert.equal(validity.valid, valid);
      assert.equal(expectedState, valid ? 'accepted' : 'rejected');
      return { status: 'PASS' };
    },
    onCase: async (record) => { records.push(record); },
  });
  assert.equal(result.status, 'PASS');
  assert.equal(result.attempts, executed.length);
  assert.equal(records.length, executed.length);
  assert.deepEqual(setup.suite.baseline, original);
  assert.equal(records.filter((record) => record.source === 'hegel:widgets').length, 4);
  assert.equal(records.filter((record) => record.source === 'hegel:annotation').length, 4);
  assert.equal(records.filter((record) => record.source === 'hegel:mixed').length, 5);
  for (const key of setup.suite.fields) {
    const isolated = records.filter((record) => record.source === `hegel:${key}` || record.source === `boundary:${key}`);
    assert.ok(isolated.length > 0);
    for (const record of isolated) for (const other of setup.suite.fields.filter((candidate) => candidate !== key)) {
      assert.equal(record.input[other], original[other]);
    }
    const boundaryRecords = records.filter((record) => record.source === `boundary:${key}`);
    assert.deepEqual(boundaryRecords.map((record) => record.input[key]), boundaryValues(setup.fields[key], original[key]));
  }
  assert.ok(records.some((record) => record.validity.valid));
  assert.ok(records.some((record) => !record.validity.valid));
});

test('case override, optional boundaries and explicit examples retain correct reporting', async () => {
  const setup = fixture();
  setup.suite.boundaries = false;
  setup.suite.examples = [{ widgets: 4, annotation: 'note' }, { widgets: 99, annotation: 'x' }];
  const records = [];
  const result = await testPropertySuite({ ...setup, cases: 2,
    executeCase: async () => ({ status: 'PASS' }), onCase: (record) => records.push(record),
  });
  assert.equal(records.filter((record) => record.source === 'hegel:mixed').length, 2);
  assert.equal(records.filter((record) => record.source.startsWith('boundary:')).length, 0);
  assert.deepEqual(records.filter((record) => record.source === 'model-example').map((record) => record.input), setup.suite.examples);
  assert.equal(result.attempts, records.length);
});

test('actual Hegel shrinks an upper-bound acceptance defect to the smallest failing input', async () => {
  const records = [];
  let caught;
  await assert.rejects(testPropertySuite({
    name: 'capacity-rule', seed: 42,
    fields: { capacity: field({ type: 'integer', required: true, min: 1, max: 4 }, { type: 'integer', min: 0, max: 6 }) },
    suite: { fields: ['capacity'], baseline: { capacity: 2 }, validState: 'accepted', invalidState: 'rejected', casesPerField: 20, cases: 2, boundaries: false },
    executeCase: async (input, expectedState) => {
      // Bug: the app accepts five or six while independently rejecting zero.
      const observedState = input.capacity >= 1 && input.capacity <= 6 ? 'accepted' : 'rejected';
      return { status: observedState === expectedState ? 'PASS' : 'FAIL', source: 'deterministic', observedState, checks: [{ passed: observedState === expectedState, description: 'capacity acceptance respects its upper limit' }] };
    },
    onCase: (record) => records.push(record),
  }), (error) => { caught = error; return error.code === 'TEST_FAILURE'; });
  assert.equal(caught.seed, 42);
  assert.deepEqual(caught.failingCase.input, { capacity: 5 });
  assert.equal(caught.failingCase.status, 'FAIL');
  assert.equal(caught.failingCase.expectedState, 'rejected');
  assert.equal(caught.failingCase.observedState, 'accepted');
  assert.equal(caught.failingCase.source, 'hegel:capacity');
  assert.equal(caught.failingCase.judgmentSource, 'deterministic');
  assert.ok(records.filter((record) => record.status === 'FAIL').every((record) => record.input.capacity > 4));
  assert.ok(records.every((record) => record.source === 'hegel:capacity'));
});

test('infrastructure uncertainty stops executor and reporting work even during Hegel reduction', async (t) => {
  for (const failure of ['returned', 'typed-error', 'untyped-error']) await t.test(failure, async () => {
    const setup = fixture();
    let calls = 0, notifications = 0, caught;
    await assert.rejects(testPropertySuite({ ...setup,
      executeCase: async () => {
        calls++;
        if (failure === 'returned') return { status: 'INCONCLUSIVE', checks: [] };
        const error = new Error('Browser or decision service unavailable');
        if (failure === 'typed-error') error.code = 'INCONCLUSIVE';
        throw error;
      },
      onCase: () => { notifications++; },
    }), (error) => { caught = error; return error.code === 'INCONCLUSIVE'; });
    assert.equal(calls, 1, 'No browser/API work should run in infrastructure shrink attempts');
    assert.equal(notifications, 1);
    assert.equal(caught.failingCase.status, 'INCONCLUSIVE');
    assert.equal(caught.seed, 42);
  });
});

test('invalid baseline and unsupported generator fail before invoking the executor', async () => {
  const setup = fixture();
  setup.suite.baseline.widgets = 0;
  await assert.rejects(testPropertySuite({ ...setup, executeCase: () => assert.fail('must not execute') }), /requires a valid baseline/);
  assert.throws(() => generatorFor(field({ type: 'string' }, { type: 'invented' }), 'valid'), /Unsupported input generator/);
});

test('text generators apply model defaults of zero minimum and one hundred maximum characters', async () => {
  // With a lower bound of 100 and the model's default upper bound of 100,
  // every result must have exactly 100 Unicode code points. Unbounded Hegel
  // text grows above 100 with this seed, so this checks actual behavior.
  const bounded = await drawValues(field({ type: 'string' }, { type: 'text', minLength: 100 }), '');
  assert.ok(bounded.length > 1);
  assert.ok(bounded.every((value) => [...value].length === 100));
  const empty = await drawValues(field({ type: 'string' }, { type: 'text', maxLength: 0 }), '');
  assert.ok(empty.length > 0);
  assert.ok(empty.every((value) => value === ''));
});

test('judgment sources never overwrite generation phases in property evidence', async () => {
  const setup = fixture();
  setup.suite.examples = [{ widgets: 2, annotation: 'valid' }];
  const records = [];
  const result = await testPropertySuite({ ...setup,
    executeCase: async () => ({ status: 'PASS', source: 'deterministic' }),
    onCase: (record) => records.push(record),
  });
  assert.equal(result.status, 'PASS');
  assert.ok(records.every((record) => record.judgmentSource === 'deterministic'));
  for (const phase of ['hegel:widgets', 'hegel:annotation', 'hegel:mixed', 'boundary:widgets', 'boundary:annotation', 'model-example']) {
    assert.ok(records.some((record) => record.source === phase), `Missing generation phase ${phase}`);
  }
});
