import test from 'node:test';
import assert from 'node:assert/strict';
import { judgeState, DecisionsError } from '../src/decisions.mjs';

const base = {
  states: [
    { id: 'form', description: 'Registration form with name, email, and age fields.' },
    { id: 'done', description: 'A registration confirmation and start again button.' },
  ],
  expectedState: 'done',
  expectation: 'The confirmation displays the submitted name and email.',
  evidence: { snapshot: 'heading: Registration confirmed\nAda, ada@example.test', url: 'http://localhost:4173/' },
  apiKey: 'test-key',
};

function payload({ choice = 'done', confidence = 0.97, probability = 0.98 } = {}) {
  return {
    answers: [
      { name: 'state', type: 'choice', choice, confidence, probabilities: [
        { value: 'form', probability: choice === 'form' ? 0.98 : 0.01 },
        { value: 'done', probability: choice === 'done' ? 0.98 : 0.01 },
        { value: '__unknown__', probability: choice === '__unknown__' ? 0.98 : 0.01 },
      ] },
      { name: 'passed', type: 'predicate', probability },
    ],
    usage: { input_tokens: 42, total_tokens: 42 },
  };
}

function respond(body = payload()) {
  return async () => ({ ok: true, status: 200, json: async () => body });
}

test('uses the Decisions contract with independent state and pass questions', async () => {
  let seen;
  const result = await judgeState({ ...base, fetchImpl: async (url, init) => {
    seen = { url, init, body: JSON.parse(init.body) };
    return { ok: true, json: async () => payload() };
  } });
  assert.equal(seen.url, 'https://api.openai.com/v1/decisions');
  assert.equal(seen.init.method, 'POST');
  assert.equal(seen.init.headers.Authorization, 'Bearer test-key');
  assert.ok(seen.init.signal instanceof AbortSignal);
  assert.equal(seen.body.model, 'gpt-6-luna');
  assert.equal(seen.body.questions[0].type, 'choice');
  assert.equal(seen.body.questions[1].type, 'predicate');
  assert.deepEqual(seen.body.questions[0].choices[0], { value: 'form', description: base.states[0].description });
  assert.equal(seen.body.questions[0].choices.at(-1).value, '__unknown__');
  assert.ok(seen.body.input.includes('UNTRUSTED_BROWSER_EVIDENCE_JSON'));
  assert.ok(!seen.body.input.includes(base.expectation));
  assert.ok(!seen.body.questions[0].instructions.includes(base.expectation));
  assert.ok(seen.body.questions[1].instructions.includes(base.expectation));
  assert.equal(result.mode, 'openai');
  assert.equal(result.observedState, 'done');
  assert.equal(result.passed, true);
  assert.equal(result.passProbability, 0.98);
  assert.equal(result.stateConfidence, 0.97);
  assert.deepEqual(result.usage, payload().usage);
});

test('optional screenshot is an inline image part, not embedded in text JSON', async () => {
  const screenshotDataUrl = 'data:image/png;base64,aW1hZ2U=';
  await judgeState({ ...base, evidence: { ...base.evidence, screenshotDataUrl }, fetchImpl: async (_, init) => {
    const { input } = JSON.parse(init.body);
    assert.equal(input[0].role, 'user');
    assert.equal(input[0].content[0].type, 'input_text');
    assert.ok(!input[0].content[0].text.includes(screenshotDataUrl));
    assert.deepEqual(input[0].content[1], { type: 'input_image', image_url: screenshotDataUrl });
    return { ok: true, json: async () => payload() };
  } });
});

test('wrong state, failed assertion, unknown state, and uncertainty never pass', async (t) => {
  for (const [name, options] of [
    ['wrong state', { choice: 'form' }],
    ['failed assertion', { probability: 0.2 }],
    ['uncertain state', { confidence: 0.84 }],
    ['uncertain assertion', { probability: 0.84 }],
    ['unknown state', { choice: '__unknown__' }],
  ]) {
    await t.test(name, async () => {
      const result = await judgeState({ ...base, fetchImpl: respond(payload(options)) });
      assert.equal(result.passed, false);
    });
  }
  const atThreshold = await judgeState({ ...base, fetchImpl: respond(payload({ confidence: 0.85, probability: 0.85 })) });
  assert.equal(atThreshold.passed, true);
});

test('matches answers by name rather than position', async () => {
  const body = payload();
  body.answers.reverse();
  const result = await judgeState({ ...base, fetchImpl: respond(body) });
  assert.equal(result.passed, true);
});

test('refusal on either question fails closed', async (t) => {
  for (const index of [0, 1]) {
    await t.test(`question ${index}`, async () => {
      const body = payload();
      body.answers[index] = { name: body.answers[index].name, type: 'refusal' };
      await assert.rejects(judgeState({ ...base, fetchImpl: respond(body) }), { name: 'DecisionsError', code: 'REFUSAL' });
    });
  }
});

test('rejects missing, duplicated, mistyped, unknown and nonfinite answers', async (t) => {
  const mutations = [
    (body) => { delete body.answers; },
    (body) => { body.answers.pop(); },
    (body) => { body.answers.push(body.answers[0]); },
    (body) => { body.answers[0].type = 'predicate'; },
    (body) => { body.answers[0].choice = 'invented'; },
    (body) => { body.answers[0].confidence = NaN; },
    (body) => { body.answers[0].confidence = 2; },
    (body) => { body.answers[1].probability = Infinity; },
    (body) => { body.answers[1].probability = '0.99'; },
    (body) => { body.answers[1].probability = -1; },
    (body) => { delete body.answers[0].probabilities; },
    (body) => { body.answers[0].probabilities = [{ value: 'done', probability: NaN }]; },
    (body) => { body.answers[0].probabilities = [{ value: 'form', probability: 1 }]; },
  ];
  for (const [index, mutate] of mutations.entries()) {
    await t.test(`malformed response ${index}`, async () => {
      const body = payload();
      mutate(body);
      await assert.rejects(judgeState({ ...base, fetchImpl: respond(body) }), { code: 'INVALID_RESPONSE' });
    });
  }
});

test('fails clearly for API access and network errors without fallback or retry', async () => {
  let calls = 0;
  await assert.rejects(judgeState({ ...base, fetchImpl: async () => {
    calls++;
    return { ok: false, status: 401 };
  } }), (error) => error instanceof DecisionsError && error.code === 'HTTP_ERROR' && error.message.includes('OPENAI_API_KEY'));
  assert.equal(calls, 1);
  await assert.rejects(judgeState({ ...base, fetchImpl: async () => { throw new Error('offline'); } }), { code: 'NETWORK_ERROR' });
  await assert.rejects(judgeState({ ...base, fetchImpl: async () => ({ ok: true, json: async () => { throw new SyntaxError('invalid'); } }) }), { code: 'INVALID_RESPONSE' });
});

test('aborts a stalled request with an actionable timeout', async () => {
  await assert.rejects(judgeState({ ...base, timeoutMs: 10, fetchImpl: async (_, { signal }) => new Promise((_, reject) => {
    signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
  }) }), { code: 'TIMEOUT' });
});

test('rejects invalid configuration before making requests', async (t) => {
  const invalid = [
    { threshold: NaN }, { threshold: Infinity }, { threshold: -0.1 }, { threshold: 1.1 },
    { timeoutMs: 0 }, { timeoutMs: 300001 }, { timeoutMs: 1.5 },
    { expectedState: 'missing' }, { states: [] },
    { states: [...base.states, base.states[0]] },
    { states: [{ id: '__unknown__', description: 'reserved' }] },
    { evidence: { snapshot: '' } },
    { evidence: { ...base.evidence, screenshotDataUrl: 'https://example.test/screenshot.png' } },
  ];
  for (const [index, override] of invalid.entries()) {
    await t.test(`invalid configuration ${index}`, async () => {
      await assert.rejects(judgeState({ ...base, ...override, fetchImpl: () => { assert.fail('must not fetch'); } }), TypeError);
    });
  }
  await assert.rejects(judgeState({ ...base, apiKey: '', fetchImpl: () => { assert.fail('must not fetch'); } }), { code: 'MISSING_API_KEY' });
});
