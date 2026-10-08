import test from 'node:test';
import assert from 'node:assert/strict';
import { observe, identifyState, checkState, inputErrors, normalizedBooking } from '../src/oracle.mjs';
import { testFormInputs } from '../src/properties.mjs';

const booking = { name: 'Ada Lovelace', email: 'ada@example.com', seats: 2 };
const review = {
  heading: 'Review your booking', text: '', snapshot: '', alerts: [],
  buttons: ['Edit details', 'Confirm booking'], fields: {},
  summary: { name: booking.name, email: booking.email, seats: '2', total: '$90' },
};

test('identifies rendered states and rejects unknown or incomplete pages', () => {
  assert.equal(identifyState(review), 'v_Review');
  assert.equal(identifyState({ heading: 'Book your workshop', buttons: ['Review booking'], alerts: [] }), 'v_Form');
  assert.equal(identifyState({ heading: 'Book your workshop', buttons: ['Review booking'], alerts: ['Check your details'] }), 'v_Error');
  assert.equal(identifyState({ heading: 'You’re on the list', buttons: ['Start again'] }), 'v_Confirmed');
  assert.equal(identifyState({ heading: 'Make something by hand', buttons: ['Book a place'] }), 'v_Home');
  assert.equal(identifyState({ ...review, buttons: [] }), '__unknown__');
  assert.equal(identifyState({ ...review, heading: 'Application error' }), '__unknown__');
});

test('review oracle catches corrupted summary fields and incorrect price', () => {
  assert.equal(checkState('v_Review', review, { booking }).passed, true);
  for (const [key, value] of [['name', 'Wrong person'], ['email', 'wrong@example.com'], ['seats', '3'], ['total', '$45']]) {
    const evidence = { ...review, summary: { ...review.summary, [key]: value } };
    assert.equal(checkState('v_Review', evidence, { booking }).passed, false);
  }
  assert.equal(checkState('v_Confirmed', review, { booking }).passed, false);
});

test('edit oracle checks preserved normalized form values', () => {
  const evidence = { heading: 'Book your workshop', buttons: ['Review booking', 'Back to home'], alerts: [], fields: { name: booking.name, email: booking.email, seats: '2' } };
  assert.equal(checkState('v_Form', evidence, { booking, previousEdge: 'e_Edit' }).passed, true);
  assert.equal(checkState('v_Form', { ...evidence, fields: { ...evidence.fields, name: '' } }, { booking, previousEdge: 'e_Edit' }).passed, false);
});

test('invalid input must show the appropriate validation error', () => {
  const invalid = { ...booking, seats: 5 };
  const evidence = { heading: 'Book your workshop', buttons: ['Review booking', 'Back to home'], alerts: ['Check your details Seats must be a whole number from 1 to 4.'], fields: { name: booking.name, email: booking.email, seats: '5' } };
  assert.equal(checkState('v_Error', evidence, { booking: invalid }).passed, true);
  assert.equal(checkState('v_Error', { ...evidence, alerts: ['Check your details Name missing'] }, { booking: invalid }).passed, false);
  assert.equal(checkState('v_Error', review, { booking: invalid }).passed, false);
});

test('independent input requirements cover boundaries, whitespace and nonintegers', () => {
  for (const seats of [1, 4, ' 2 ']) assert.deepEqual(inputErrors({ ...booking, seats }), []);
  for (const seats of [-1, 0, 5, 6, 1.5, 'two', '']) assert.equal(inputErrors({ ...booking, seats }).length, 1);
  for (const length of [0, 1, 61]) assert.equal(inputErrors({ ...booking, name: 'N'.repeat(length) }).length, 1);
  for (const length of [2, 60]) assert.deepEqual(inputErrors({ ...booking, name: 'N'.repeat(length) }), []);
  for (const email of ['', 'no-at', 'a@localhost', 'a @example.com']) assert.equal(inputErrors({ ...booking, email }).length, 1);
  assert.deepEqual(inputErrors({ name: '  Ada  ', email: '  ada@example.com  ', seats: 2 }), []);
});

test('observation parses MCP evaluate JSON and preserves the accessibility snapshot', async () => {
  const browser = {
    snapshot: async () => '### Snapshot\n- heading "Review your booking"',
    call: async (tool, args) => {
      assert.equal(tool, 'browser_evaluate');
      assert.ok(!args.function.includes('dataset'));
      return { text: `### Result\n${JSON.stringify(review)}\n### Ran Playwright code\n...` };
    },
  };
  const evidence = await observe(browser);
  assert.equal(evidence.heading, review.heading);
  assert.match(evidence.snapshot, /### Snapshot/);
});

test('malformed browser observations fail closed', async () => {
  for (const text of ['unexpected response', '### Result\nnot json', '### Result\n{}']) {
    await assert.rejects(observe({ snapshot: async () => '', call: async () => ({ text }) }));
  }
});

// A controlled browser double tests Hegel's async lifecycle and reporting. The
// deliberate five-seat acceptance reproduces the demo's optional mutation.
function propertyBrowser({ acceptFive = false } = {}) {
  return {
    navigations: 0, submissions: 0, resetSinceSubmit: false,
    async navigate() { this.navigations++; this.resetSinceSubmit = true; this.input = null; },
    async click(name) {
      if (name === 'Review booking') {
        assert.equal(this.resetSinceSubmit, true, 'every case and shrink must reset the app');
        this.resetSinceSubmit = false;
        this.submissions++;
      }
    },
    async fill(input) { this.input = input; },
    async snapshot() { return 'Browser test double'; },
    async call() {
      const values = normalizedBooking(this.input);
      const errors = inputErrors(this.input);
      const accepted = errors.length === 0 || (acceptFive && values.seats === '5');
      const evidence = accepted ? {
        ...review,
        summary: { ...values, total: `$${Number(values.seats) * 45}` },
      } : {
        heading: 'Book your workshop', buttons: ['Review booking', 'Back to home'],
        fields: values, alerts: [`Check your details ${errors.join(' ')}`],
      };
      return { text: `### Result\n${JSON.stringify(evidence)}` };
    },
  };
}

test('Hegel reports and shrinks the seeded five-seat defect, resetting for every replay', async () => {
  const browser = propertyBrowser({ acceptFive: true });
  const attempts = [];
  await assert.rejects(testFormInputs({ browser, url: 'http://demo.test', seed: 42, onCase: (record) => attempts.push(record) }), (error) => {
    assert.equal(error.failingCase.source, 'hegel-seats');
    assert.equal(error.failingCase.input.seats, 5);
    assert.equal(error.failingCase.observedState, 'v_Review');
    assert.equal(error.seed, 42);
    return true;
  });
  assert.ok(attempts.filter((record) => !record.passed).length >= 2, 'Hegel replays its failing input');
  assert.equal(browser.navigations, browser.submissions);
  assert.equal(attempts.length, browser.submissions);
});

test('successful properties report generated and guaranteed boundaries and leave a fresh form', async () => {
  const browser = propertyBrowser();
  const result = await testFormInputs({ browser, url: 'http://demo.test', testCases: 4 });
  assert.equal(result.passed, true);
  assert.ok(result.cases.some((record) => record.source === 'hegel-mixed'));
  assert.deepEqual(result.cases.filter((record) => record.source === 'boundary').map((record) => record.input.seats), [1, 4, 0, 5]);
  assert.equal(browser.navigations, browser.submissions + 1);
  assert.equal(browser.input, null);
});
