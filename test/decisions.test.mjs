import test from 'node:test';
import assert from 'node:assert/strict';
import { DecisionService, DecisionsError } from '../src/decisions.mjs';

const base = {
  states: [{ id: 'entry', description: 'An editable input form.' }, { id: 'done', description: 'A completion message.' }],
  expectedState: 'done',
  expectation: 'The completion message shows the submitted value.',
  evidence: { snapshot: '- heading "Complete" [ref=e1]\n- text: Example value', url: 'http://localhost:4173/' },
};
const target = {
  intent: 'Proceed to the review',
  candidates: [{ id: 'e2', role: 'button', name: 'Back' }, { id: 'e3', role: 'button', name: 'Continue' }],
  snapshot: '- button "Back" [ref=e2]\n- button "Continue" [ref=e3]',
};
const choice = (name, selected, values, { confidence = 0.98, selectedProbability = 0.98 } = {}) => ({
  name, type: 'choice', choice: selected, confidence,
  probabilities: values.map((value) => ({ value, probability: value === selected ? selectedProbability : (1 - selectedProbability) / (values.length - 1) })),
});
function judgment({ selected = 'done', confidence, selectedProbability, probability = 0.98 } = {}) {
  return {
    answers: [choice('state', selected, ['entry', 'done', '__unknown__'], { confidence, selectedProbability }), { name: 'passed', type: 'predicate', probability }],
    usage: { input_tokens: 42, output_tokens: 2, total_tokens: 44 },
  };
}
function targeting(options) {
  return { answers: [choice('target', options?.selected ?? 'c1', ['c0', 'c1', '__unknown__'], options)] };
}
const respond = (payload) => async () => ({ ok: true, status: 200, json: async () => payload });
const service = (payload = judgment(), options = {}) => new DecisionService({ apiKey: 'test-key', fetchImpl: respond(payload), ...options });
const inconclusive = (reason) => (error) => error instanceof DecisionsError && error.code === 'INCONCLUSIVE' && error.reason === reason;

test('sends independent state and assertion questions using the Decisions contract', async () => {
  let seen;
  const decisions = service(undefined, { fetchImpl: async (url, init) => {
    seen = { url, init, body: JSON.parse(init.body) };
    return { ok: true, json: async () => judgment() };
  } });
  const result = await decisions.judge(base);
  assert.equal(seen.url, 'https://api.openai.com/v1/decisions');
  assert.equal(seen.init.method, 'POST');
  assert.equal(seen.init.headers.Authorization, 'Bearer test-key');
  assert.ok(seen.init.signal instanceof AbortSignal);
  assert.equal(seen.body.model, 'gpt-6-luna');
  assert.deepEqual(seen.body.questions.map(({ name, type }) => ({ name, type })), [{ name: 'state', type: 'choice' }, { name: 'passed', type: 'predicate' }]);
  assert.deepEqual(seen.body.questions[0].choices[0], { value: 'entry', description: base.states[0].description });
  assert.equal(seen.body.questions[0].choices.at(-1).value, '__unknown__');
  assert.ok(seen.body.input.includes('UNTRUSTED_BROWSER_EVIDENCE_JSON'));
  assert.ok(!seen.body.input.includes(base.expectation));
  assert.ok(!seen.body.questions[0].instructions.includes(base.expectation));
  assert.ok(!seen.body.questions[0].instructions.includes(base.expectedState));
  assert.ok(seen.body.questions[1].instructions.includes(base.expectation));
  assert.equal(result.status, 'PASS');
  assert.equal(result.observedState, 'done');
  assert.equal(result.passProbability, 0.98);
  assert.equal(result.stateProbability, 0.98);
  assert.deepEqual(decisions.stats, { calls: 1, cacheHits: 0, inputTokens: 42, outputTokens: 2, totalTokens: 44 });
});

test('optional screenshots use image input parts', async () => {
  const screenshotDataUrl = 'data:image/png;base64,aW1hZ2U=';
  const decisions = service(undefined, { fetchImpl: async (_, init) => {
    const { input } = JSON.parse(init.body);
    assert.equal(input[0].role, 'user');
    assert.equal(input[0].content[0].type, 'input_text');
    assert.ok(!input[0].content[0].text.includes(screenshotDataUrl));
    assert.deepEqual(input[0].content[1], { type: 'input_image', image_url: screenshotDataUrl });
    return { ok: true, json: async () => judgment() };
  } });
  await decisions.judge({ ...base, evidence: { ...base.evidence, screenshotDataUrl } });
});

test('supports configurable Decisions-compatible endpoints and model', async () => {
  const decisions = service(undefined, { endpoint: 'https://provider.example/v1/decisions', model: 'classifier', fetchImpl: async (url, init) => {
    assert.equal(url, 'https://provider.example/v1/decisions');
    assert.equal(JSON.parse(init.body).model, 'classifier');
    return { ok: true, json: async () => judgment() };
  } });
  assert.equal((await decisions.judge(base)).status, 'PASS');
});

test('requires both choice confidence and selected probability, distinguishing failure from uncertainty', async (t) => {
  const cases = [
    ['confident wrong state', { selected: 'entry' }, 'FAIL'],
    ['confident failed predicate', { probability: 0.15 }, 'FAIL'],
    ['uncertain predicate', { probability: 0.2 }, 'INCONCLUSIVE'],
    ['uncertain state confidence', { confidence: 0.84 }, 'INCONCLUSIVE'],
    ['uncertain selected probability', { selectedProbability: 0.84 }, 'INCONCLUSIVE'],
    ['uncertain assertion', { probability: 0.84 }, 'INCONCLUSIVE'],
    ['unknown state', { selected: '__unknown__' }, 'INCONCLUSIVE'],
    ['wrong state but uncertain choice probability', { selected: 'entry', selectedProbability: 0.6 }, 'INCONCLUSIVE'],
    ['at pass threshold', { confidence: 0.85, selectedProbability: 0.85, probability: 0.85 }, 'PASS'],
  ];
  for (const [name, options, expected] of cases) await t.test(name, async () => {
    assert.equal((await service(judgment(options)).judge(base)).status, expected);
  });
});

test('matches named answers regardless of response order', async () => {
  const body = judgment();
  body.answers.reverse();
  assert.equal((await service(body).judge(base)).status, 'PASS');
});

test('maps chosen stable candidate indices back to current MCP refs after a cache hit', async () => {
  let calls = 0;
  const decisions = service(undefined, { fetchImpl: async (_, init) => {
    calls++;
    const body = JSON.parse(init.body);
    assert.deepEqual(body.questions[0].choices.map(({ value }) => value), ['c0', 'c1', '__unknown__']);
    assert.ok(!JSON.stringify(body).includes('e3'));
    return { ok: true, json: async () => targeting() };
  } });
  assert.equal((await decisions.chooseTarget(target)).targetId, 'e3');
  const next = { ...target, candidates: target.candidates.map((item, i) => ({ ...item, id: `e${100 + i}` })), snapshot: target.snapshot.replaceAll('e2', 'e100').replaceAll('e3', 'e101') };
  const cached = await decisions.chooseTarget(next);
  assert.equal(cached.targetId, 'e101');
  assert.equal(cached.source, 'cache');
  assert.equal(decisions.stats.cacheHits, 1);
  assert.equal(calls, 1);
});

test('cache keys include complete assertion, state definitions, intent, candidate meaning and evidence', async () => {
  let calls = 0;
  const decisions = service(undefined, { fetchImpl: async (_, init) => {
    calls++;
    const body = JSON.parse(init.body);
    return { ok: true, json: async () => body.questions[0].name === 'target' ? targeting() : judgment() };
  } });
  const mutable = structuredClone(base);
  await decisions.judge(mutable);
  mutable.expectation = 'Different assertion';
  await decisions.judge(mutable);
  mutable.evidence.snapshot += '\n- alert "Invalid value"';
  await decisions.judge(mutable);
  mutable.states[0].description = 'A changed state definition';
  await decisions.judge(mutable);
  await decisions.chooseTarget(target);
  await decisions.chooseTarget({ ...target, intent: 'Different intent' });
  await decisions.chooseTarget({ ...target, candidates: target.candidates.map((item) => ({ ...item, description: 'Additional constraint' })) });
  await decisions.chooseTarget({ ...target, snapshot: target.snapshot + '\n- button "Continue" [disabled]' });
  assert.equal(calls, 8);
  assert.equal(decisions.stats.cacheHits, 0);
});

test('cached independent observations are re-evaluated against the current expected state', async () => {
  const decisions = service();
  assert.equal((await decisions.judge(base)).status, 'PASS');
  const wrong = await decisions.judge({ ...base, expectedState: 'entry' });
  assert.equal(wrong.status, 'FAIL');
  assert.equal(wrong.observedState, 'done');
  assert.equal(decisions.stats.calls, 1);
  assert.equal(decisions.stats.cacheHits, 1);
  assert.equal(decisions.stats.inputTokens, 42);
  wrong.usage.input_tokens = 999;
  assert.equal((await decisions.judge(base)).usage.input_tokens, 42);
});

test('uncertain, unknown and refused target choices are typed inconclusive outcomes', async (t) => {
  for (const options of [{ confidence: 0.8 }, { selectedProbability: 0.6 }, { selected: '__unknown__' }]) {
    await t.test(JSON.stringify(options), async () => assert.rejects(service(targeting(options)).chooseTarget(target), inconclusive('UNCERTAIN_TARGET')));
  }
  await assert.rejects(service({ answers: [{ name: 'target', type: 'refusal' }] }).chooseTarget(target), inconclusive('REFUSAL'));
});

test('offline mode makes no semantic guesses and never sends a request', async () => {
  const decisions = service(undefined, { mode: 'offline', fetchImpl: () => assert.fail('must not fetch') });
  await assert.rejects(decisions.chooseTarget(target), inconclusive('OFFLINE'));
  assert.equal((await decisions.judge(base)).status, 'INCONCLUSIVE');
  assert.equal(decisions.stats.calls, 0);
});

test('refusal on either judgment question fails closed', async (t) => {
  for (const index of [0, 1]) await t.test(`question ${index}`, async () => {
    const body = judgment();
    body.answers[index] = { name: body.answers[index].name, type: 'refusal' };
    await assert.rejects(service(body).judge(base), inconclusive('REFUSAL'));
  });
});

test('malformed answers cannot produce pass or fail judgments', async (t) => {
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
    (body) => { body.answers[0].probabilities = [{ value: 'entry', probability: 1 }]; },
    (body) => { body.answers[0].probabilities.push(body.answers[0].probabilities[0]); },
  ];
  for (const [index, mutate] of mutations.entries()) await t.test(`malformed response ${index}`, async () => {
    const body = judgment();
    mutate(body);
    await assert.rejects(service(body).judge(base), inconclusive('INVALID_RESPONSE'));
  });
});

test('caps actual requests, permits cached calls at the limit, and never retries errors', async () => {
  const decisions = service(undefined, { maxCalls: 1 });
  await decisions.judge(base);
  await decisions.judge(base);
  await assert.rejects(decisions.judge({ ...base, expectation: 'Uncached assertion' }), inconclusive('CALL_BUDGET'));
  assert.equal(decisions.stats.calls, 1);
  assert.equal(decisions.stats.cacheHits, 1);
  let calls = 0;
  const failing = service(undefined, { maxCalls: 1, fetchImpl: async () => { calls++; throw new Error('offline'); } });
  await assert.rejects(failing.judge(base), inconclusive('NETWORK_ERROR'));
  await assert.rejects(failing.judge(base), inconclusive('CALL_BUDGET'));
  assert.equal(calls, 1);
});

test('API, JSON, missing key and timeout errors become explicit inconclusive outcomes', async () => {
  await assert.rejects(service(undefined, { fetchImpl: async () => ({ ok: false, status: 401 }) }).judge(base), inconclusive('HTTP_ERROR'));
  await assert.rejects(service(undefined, { fetchImpl: async () => ({ ok: true, json: async () => { throw new SyntaxError('invalid'); } }) }).judge(base), inconclusive('INVALID_RESPONSE'));
  await assert.rejects(service(undefined, { apiKey: '' }).judge(base), inconclusive('MISSING_API_KEY'));
  await assert.rejects(service(undefined, { timeoutMs: 10, fetchImpl: async (_, { signal }) => new Promise((_, reject) => {
    signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
  }) }).judge(base), inconclusive('TIMEOUT'));
});

test('too many choices are inconclusive before request submission', async () => {
  const decisions = service(undefined, { fetchImpl: () => assert.fail('must not fetch') });
  const excess = Array.from({ length: 255 }, (_, i) => ({ id: `s${i}`, description: 'State', role: 'button', name: 'Action' }));
  await assert.rejects(decisions.chooseTarget({ ...target, candidates: excess }), inconclusive('TOO_MANY_CHOICES'));
  await assert.rejects(decisions.judge({ ...base, states: excess, expectedState: 's0' }), inconclusive('TOO_MANY_CHOICES'));
  assert.equal(decisions.stats.calls, 0);
});

test('rejects malformed configuration and model data before sending requests', async (t) => {
  for (const options of [{ mode: 'invented' }, { threshold: NaN }, { threshold: 0.5 }, { threshold: 1.1 }, { maxCalls: -1 }, { maxCalls: 1.5 }, { timeoutMs: 0 }, { endpoint: 'file:///tmp/test' }]) {
    assert.throws(() => service(undefined, options), TypeError);
  }
  for (const override of [
    { expectedState: 'missing' }, { states: [] }, { states: [...base.states, base.states[0]] },
    { states: [{ id: '__unknown__', description: 'reserved' }] }, { evidence: { snapshot: '' } },
    { evidence: { ...base.evidence, screenshotDataUrl: 'https://example.test/screenshot.png' } },
  ]) await t.test(JSON.stringify(override), async () => {
    await assert.rejects(service(undefined, { fetchImpl: () => assert.fail('must not fetch') }).judge({ ...base, ...override }), TypeError);
  });
});
