import * as hegel from '@hegeldev/hegel';
import * as gs from '@hegeldev/hegel/generators';
import { observe, checkState, inputErrors } from './oracle.mjs';

export async function testFormInputs({ browser, url, seed = 42, testCases = 12, onCase = () => {} }) {
  if (!Number.isInteger(testCases) || testCases < 1 || testCases > 1000) throw new TypeError('testCases must be an integer from 1 to 1000.');
  const cases = [];
  let lastFailure;
  const reset = async () => { await browser.navigate(url); await browser.click('Book a place'); };
  const verify = async (input, source) => {
    let record;
    try {
      // Every generation and every shrink gets a clean browser/application state.
      await reset();
      await browser.fill(input);
      await browser.click('Review booking');
      const evidence = await observe(browser);
      const expectedState = inputErrors(input).length ? 'v_Error' : 'v_Review';
      record = { source, input, expectedState, ...checkState(expectedState, evidence, { booking: input }) };
      if (!record.passed) {
        const failure = new Error(`Form property failed for ${JSON.stringify(input)}: ${record.checks.filter((item) => !item.passed).map((item) => item.description).join('; ')}`);
        failure.input = input;
        throw failure;
      }
    } catch (error) {
      record ??= { source, input, passed: false, observedState: '__unknown__', checks: [] };
      record.error = error.message;
      lastFailure = record;
      throw error;
    } finally {
      cases.push(record);
      await onCase(record);
    }
  };
  const settings = {
    seed, testCases, database: hegel.Database.disabled,
    suppressHealthCheck: [hegel.HealthCheck.TooSlow], reportMultipleFailures: false,
  };
  try {
    // One dimension isolates and shrinks the opt-in seat-limit defect to seats=5.
    await hegel.testAsync(async (tc) => {
      const input = { name: 'Ada Lovelace', email: 'ada@example.com', seats: tc.draw(gs.integers({ minValue: 0, maxValue: 6 })) };
      tc.note(JSON.stringify(input));
      await verify(input, 'hegel-seats');
    }, { ...settings, testCases: 16 });

    await hegel.testAsync(async (tc) => {
      const input = {
        name: tc.draw(gs.sampledFrom(['Ada Lovelace', '  Ada Lovelace  ', '', 'A', 'Al', 'N'.repeat(60), 'N'.repeat(61), "Jo <&> O’Neil"])),
        email: tc.draw(gs.sampledFrom(['ada@example.com', '  ada@example.com  ', 'ada+work@example.test', '', 'missing-at', 'ada@localhost', 'ada @example.com'])),
        seats: tc.draw(gs.oneOf(gs.integers({ minValue: -1, maxValue: 6 }), gs.sampledFrom(['1.5', 'two', '', ' 2 ']))),
      };
      tc.note(JSON.stringify(input));
      await verify(input, 'hegel-mixed');
    }, settings);

    // Guaranteed boundaries supplement generated cases; they never replace Hegel.
    for (const seats of [1, 4, 0, 5]) {
      await verify({ name: 'Ada Lovelace', email: 'ada@example.com', seats }, 'boundary');
    }
    await reset();
    return { passed: true, seed, cases, attempts: cases.length };
  } catch (error) {
    error.failingCase = lastFailure;
    error.cases = cases;
    error.seed = seed;
    throw error;
  }
}
