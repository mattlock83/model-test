import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveValue, computeDerived, evaluateAssertions, classifyState, validateInput } from '../src/declarative.mjs';

const context = { input: { quantity: '3', customer: '  Ada  ' }, derived: { unitPrice: 12 } };
const evidence = {
  text: 'Review order\nAda\nTotal: $36', snapshot: '- heading "Review order"',
  nodes: [
    { role: 'heading', name: 'Review order', ref: 'e1' },
    { role: 'button', name: 'Confirm order', ref: 'e2' },
    { role: 'textbox', name: 'Customer', value: 'Ada', ref: 'e3' },
    { role: 'spinbutton', name: 'Quantity', value: '3', ref: 'e4' },
  ],
};

test('JSON templates preserve exact variable types and resolve nested arithmetic', () => {
  const derived = computeDerived({
    customer: { trim: { var: 'input.customer' } },
    quantity: { number: '{{input.quantity}}' },
    total: { multiply: [{ var: 'derived.unitPrice' }, { var: 'derived.quantity' }] },
    feeTotal: { add: [{ var: 'derived.total' }, 4] },
  }, context);
  assert.deepEqual(derived, { unitPrice: 12, customer: 'Ada', quantity: 3, total: 36, feeTotal: 40 });
  assert.deepEqual(resolveValue({ nested: ['{{derived.quantity}}', 'Total: ${{derived.total}}', { label: '{{derived.customer}}' }] }, { ...context, derived }), {
    nested: [3, 'Total: $36', { label: 'Ada' }],
  });
});

test('context values are data and never interpreted as expressions or templates', () => {
  const unsafeLookingData = { text: '{{missing.path}}', number: 'not-an-expression' };
  assert.deepEqual(resolveValue({ var: 'input.data' }, { input: { data: unsafeLookingData } }), unsafeLookingData);
  assert.throws(() => resolveValue('Content {{input.data}}', { input: { data: unsafeLookingData } }), /non-scalar/);
});

test('missing variables, inherited properties and prototype paths are rejected', () => {
  for (const value of ['{{input.missing}}', { var: 'input.constructor' }, { var: '__proto__.x' }, { var: 'input.prototype' }]) {
    assert.throws(() => resolveValue(value, context), /Missing|Unsafe/);
  }
  assert.throws(() => resolveValue('{{input.inherited}}', { input: Object.create({ inherited: 2 }) }), /Missing/);
  assert.throws(() => resolveValue(JSON.parse('{"__proto__": {"polluted":true}}')), /Unsafe/);
  assert.throws(() => computeDerived({ total: '{{derived.later}}', later: 4 }), /Missing/);
});

test('invalid expressions and excessive inputs fail closed', () => {
  for (const value of [{ op: 'execute', code: 'throw 1' }, { multiply: [], var: 'input.quantity' }, { multiply: [] }, { number: '' }, { number: '0x10' }, { number: false }, { trim: null }, { multiply: [1e308, 1e308] }, { multiply: Array(65).fill(1) }]) {
    assert.throws(() => resolveValue(value, context));
  }
  let deep = 0;
  for (let i = 0; i < 34; i++) deep = [deep];
  assert.throws(() => resolveValue(deep), /bounds/);
  assert.throws(() => resolveValue('x'.repeat(16385)), /bounds/);
  assert.throws(() => resolveValue(Array(10001).fill(null)), /bounds/);
});

test('generic assertions use accessible roles, rendered text and field values', () => {
  const checks = evaluateAssertions([
    { type: 'visible', target: { role: 'Heading', name: ' review   ORDER ' } },
    { type: 'absent', target: { role: 'alert' } },
    { type: 'text', contains: 'Total: $36' },
    { type: 'notText', contains: 'Internal error' },
    { type: 'value', target: { role: 'textbox', name: 'Customer' }, equals: { trim: '{{input.customer}}' } },
    { type: 'value', target: { role: 'spinbutton', name: 'Quantity' }, equals: { number: '{{input.quantity}}' } },
    { type: 'count', target: { role: 'button' }, equals: 1 },
  ], evidence, context);
  assert.equal(checks.length, 7);
  assert.ok(checks.every((check) => check.passed));
  const failed = evaluateAssertions([
    { type: 'text', contains: 'Total: $12' },
    { type: 'value', target: { role: 'spinbutton', name: 'Quantity' }, equals: 4 },
  ], evidence, context);
  assert.ok(failed.every((check) => !check.passed));
});

test('ambiguous field targets and unavailable values never pass', () => {
  const assertion = [{ type: 'value', target: { role: 'textbox', name: 'Customer' }, equals: 'Ada' }];
  assert.equal(evaluateAssertions(assertion, { ...evidence, nodes: [...evidence.nodes, { role: 'textbox', name: 'Customer', value: 'Ada' }] })[0].passed, false);
  assert.equal(evaluateAssertions(assertion, { ...evidence, nodes: [{ role: 'textbox', name: 'Customer' }] })[0].passed, false);
  assert.equal(evaluateAssertions(assertion, { ...evidence, nodes: [] })[0].passed, false);
});

test('unknown assertions and malformed requirements are errors, not passing checks', () => {
  for (const assertion of [
    { type: 'javascript', code: 'true', when: { exists: 'input.absent' } },
    { type: 'text', contains: '' },
    { type: 'count', target: { role: 'button' }, equals: -1 },
    { type: 'visible', target: { name: 'Confirm order' } },
    { type: 'visible', target: { role: 'button' }, when: { truthy: 'input.customer' } },
  ]) assert.throws(() => evaluateAssertions([assertion], evidence, context));
  assert.throws(() => evaluateAssertions([{ type: 'text', contains: 'something' }], { nodes: [] }), /visible text/);
});

test('state classification requires a unique nonempty set of successful checks', () => {
  const review = { id: 'review', match: [{ type: 'visible', target: { role: 'heading', name: 'Review order' } }] };
  const edit = { id: 'edit', match: [{ type: 'visible', target: { role: 'heading', name: 'Edit order' } }] };
  assert.equal(classifyState([review, edit], evidence), 'review');
  assert.equal(classifyState([edit], evidence), '__unknown__');
  assert.equal(classifyState([review, { ...review, id: 'alsoReview' }], evidence), '__unknown__');
  assert.equal(classifyState([{ id: 'empty', match: [] }], evidence), '__unknown__');
  const conditional = { type: 'text', contains: '{{input.missing}}', when: { exists: 'input.missing' } };
  assert.deepEqual(evaluateAssertions([conditional], evidence, context), []);
  assert.equal(classifyState([{ id: 'skipped', match: [conditional] }], evidence, context), '__unknown__');
  assert.throws(() => evaluateAssertions([{ ...conditional, when: { exists: 'input.__proto__' } }], evidence, context), /Unsafe/);
});

const fields = {
  customer: { constraints: { type: 'string', required: true, trim: true, minLength: 2, maxLength: 8 } },
  contact: { constraints: { type: 'email', required: true, trim: true } },
  quantity: { constraints: { type: 'integer', required: true, min: 1, max: 4 } },
};
const valid = { customer: 'Ada', contact: 'ada@example.com', quantity: 2 };

test('schema-aware derivations depend only on their own form inputs', () => {
  const definitions = {
    amount: { multiply: [{ var: 'input.quantity' }, 12] },
    amountWithFee: { add: [{ var: 'derived.amount' }, 4] },
    greeting: 'Hello {{input.customer}}',
    contactLine: 'Email: {{input.contact}}',
    fixedLabel: 'Dispatch summary',
  };
  assert.deepEqual(computeDerived(definitions, { input: { quantity: '3' } }, fields), {
    amount: 36, amountWithFee: 40, fixedLabel: 'Dispatch summary',
  });
  assert.deepEqual(computeDerived(definitions, { input: { customer: 'Ada', contact: 'invalid', quantity: 5 } }, fields), {
    greeting: 'Hello Ada', fixedLabel: 'Dispatch summary',
  });
  assert.deepEqual(computeDerived(definitions, { input: {} }, fields), { fixedLabel: 'Dispatch summary' });
});

test('schema-aware derivations skip transitive unavailable values and discard stale results', () => {
  const definitions = {
    quantity: { number: '{{input.quantity}}' },
    amount: { multiply: [{ var: 'derived.quantity' }, { var: 'derived.unitPrice' }] },
    label: 'Amount: {{derived.amount}}',
  };
  const stale = { input: { quantity: 'nope' }, derived: { unitPrice: 12, quantity: 2, amount: 24, label: 'Amount: 24' } };
  assert.deepEqual(computeDerived(definitions, stale, fields), { unitPrice: 12 });
  assert.equal(stale.derived.amount, 24, 'the caller context is not mutated');
  assert.deepEqual(computeDerived(definitions, { ...stale, input: { quantity: 1 } }, fields), {
    unitPrice: 12, quantity: 1, amount: 12, label: 'Amount: 12',
  });
});

test('schema-aware derivation does not suppress invalid models or constant arithmetic errors', () => {
  for (const definitions of [
    { bad: '{{input.undeclared}}' },
    { bad: '{{input.constructor}}' },
    { bad: '{{derived.later}}', later: 4 },
    { bad: { add: [{ var: 'input.quantity' }, { var: 'derived.unknown' }] } },
    { bad: { add: [{ var: 'input.quantity' }, 'not a number'] } },
    { bad: { add: [{ var: 'input.quantity' }, { number: 'invalid' }] } },
    { bad: { multiply: [], var: 'input.quantity' } },
    { bad: { op: 'execute', value: '{{input.quantity}}' } },
  ]) assert.throws(() => computeDerived(definitions, { input: {} }, fields));
  assert.throws(() => computeDerived({ label: 'constant' }, {}, { bad: { constraints: { typo: true } } }), /Unknown input constraint/);
  assert.throws(() => computeDerived({ bad: '{{baseUrl}}' }, {}, fields), /Missing model variable/);
  assert.throws(() => computeDerived({ bad: { number: '{{input.customer}}' } }, { input: { customer: 'Ada' } }, fields), /finite decimal/);
});

test('strict derivation behavior is unchanged and input strings remain literal data', () => {
  assert.throws(() => computeDerived({ total: { number: '{{input.quantity}}' } }, { input: {} }), /Missing model variable/);
  const schema = { memo: { constraints: { type: 'string', required: true } } };
  assert.deepEqual(computeDerived({ literal: '{{input.memo}}' }, { input: { memo: '{{input.notDeclared}}' } }, schema), { literal: '{{input.notDeclared}}' });
});

test('model constraints independently check inclusive input boundaries', () => {
  assert.deepEqual(validateInput(fields, valid), { valid: true, violations: [] });
  for (const quantity of [1, 4, '2', ' 2 ', '2.0']) assert.equal(validateInput(fields, { ...valid, quantity }).valid, true);
  for (const quantity of [0, 5, 1.5, '', false, 'two', '0x2', Infinity]) assert.equal(validateInput(fields, { ...valid, quantity }).valid, false);
  for (const customer of ['AB', '12345678', '  Ada  ', '😀😀']) assert.equal(validateInput(fields, { ...valid, customer }).valid, true);
  for (const customer of ['', ' ', 'A', '123456789', 123]) assert.equal(validateInput(fields, { ...valid, customer }).valid, false);
  for (const contact of ['', 'missing-at', 'a@localhost', 'a @example.com', 'a@x..com', 'a@@x.com']) assert.equal(validateInput(fields, { ...valid, contact }).valid, false);
  assert.equal(validateInput(fields, { ...valid, contact: ' ada@example.com ' }).valid, true);
});

test('validation reports field and violated rule with model-provided messages', () => {
  const schema = { amount: { constraints: { type: 'number', required: true, min: 0, max: 10, message: 'Enter an amount from zero to ten.' } } };
  assert.deepEqual(validateInput(schema, { amount: 11 }), { valid: false, violations: [{ field: 'amount', rule: 'max', message: 'Enter an amount from zero to ten.' }] });
  assert.equal(validateInput(schema, { amount: '2.5' }).valid, true);
});

test('patterns constrain complete values and optional fields accept emptiness', () => {
  const schema = { code: { constraints: { type: 'string', pattern: '[A-Z]{2}[0-9]{2}' } } };
  for (const code of [undefined, null, '', 'AB12']) assert.equal(validateInput(schema, { code }).valid, true);
  for (const code of ['xAB12', 'AB12x', 'AB', 'ab12']) assert.equal(validateInput(schema, { code }).valid, false);
  const integer = { count: { constraints: { type: 'integer', required: true, pattern: '[0-9]+' } } };
  assert.equal(validateInput(integer, { count: '2.0' }).valid, false);
  assert.equal(validateInput(integer, { count: '2' }).valid, true);
});

test('invalid constraint definitions are rejected even when the input is empty', () => {
  for (const constraints of [
    { type: 'unsupported' }, { minimum: 2 }, { min: '1' }, { min: 3, max: 2 },
    { minLength: 1.5 }, { minLength: 4, maxLength: 2 }, { required: 'true' },
    { pattern: '[' }, { pattern: '(a+)+' }, { pattern: 'x'.repeat(257) },
  ]) assert.throws(() => validateInput({ arbitrary: { constraints } }, {}));
});
