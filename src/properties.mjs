import * as hegel from '@hegeldev/hegel';
import * as gs from '@hegeldev/hegel/generators';
import { validateInput } from './declarative.mjs';

/** Boundary partitions come from model requirements, not from application code. */
export function boundaryValues(field, baseline) {
  const c = field.constraints;
  let values = [baseline, ''];
  if (c.type === 'integer' || c.type === 'number') {
    for (const value of [c.min, c.max]) if (value !== undefined) values.push(value - 1, value, value + 1);
    values.push('1.5', 'not-a-number');
  } else if (c.type === 'email') {
    values.push('user@example.test', 'missing-at', 'user@localhost', 'user @example.test');
  } else {
    for (const length of [c.minLength, c.maxLength]) if (length !== undefined) {
      for (const delta of [-1, 0, 1]) values.push('x'.repeat(Math.max(0, length + delta)));
    }
    values.push(`  ${baseline}  `);
  }
  return [...new Map(values.map((value) => [JSON.stringify(value), value])).values()];
}

export function generatorFor(field, baseline) {
  const spec = field.generator;
  if (!spec) return gs.sampledFrom(boundaryValues(field, baseline));
  switch (spec.type) {
    case 'sample': return gs.sampledFrom(spec.values);
    case 'integer': return gs.integers({ minValue: spec.min, maxValue: spec.max });
    case 'number': return gs.floats({ minValue: spec.min, maxValue: spec.max, allowNan: false, allowInfinity: false });
    case 'text': return gs.text({ minSize: spec.minLength ?? 0, maxSize: spec.maxLength ?? 100 });
    case 'oneOf': return gs.oneOf(...spec.generators.map((generator) => generatorFor({ ...field, generator }, baseline)));
    default: throw new Error(`Unsupported input generator: ${spec.type}`);
  }
}

/** The injected executor uses the same generic model actions as the graph walk. */
export async function testPropertySuite({ name, suite, fields, seed, cases, executeCase, onCase = () => {} }) {
  const definitions = Object.fromEntries(suite.fields.map((key) => [key, fields[key]]));
  if (!validateInput(definitions, suite.baseline).valid) throw new Error(`Property ${name} requires a valid baseline.`);
  const generators = Object.fromEntries(suite.fields.map((key) => [key, generatorFor(fields[key], suite.baseline[key])]));
  let infrastructureError, lastFailure;
  const attempts = [];
  const verify = async (input, source) => {
    // Infrastructure uncertainty is not a property counterexample. Stop browser/API
    // work immediately even if Hegel requests additional internal reduction calls.
    if (infrastructureError) throw infrastructureError;
    let record;
    try {
      const validity = validateInput(definitions, input);
      const expectedState = validity.valid ? suite.validState : suite.invalidState;
      const { source: judgmentSource, ...result } = await executeCase(input, expectedState, validity);
      record = { ...result, suite: name, source, input, expectedState, validity,
        ...(judgmentSource === undefined ? {} : { judgmentSource }) };
      if (record.status !== 'PASS') {
        const error = new Error(`Property ${name}: ${record.status} for ${JSON.stringify(input)}. ${record.checks?.filter((check) => !check.passed).map((check) => check.description).join('; ') || ''}`);
        error.code = record.status === 'FAIL' ? 'TEST_FAILURE' : 'INCONCLUSIVE';
        throw error;
      }
    } catch (error) {
      record ??= { suite: name, source, input, status: 'INCONCLUSIVE', passed: false, error: error.message };
      record.error = error.message;
      lastFailure = record;
      if (error.code !== 'TEST_FAILURE') infrastructureError = error;
      throw error;
    } finally {
      attempts.push(record);
      await onCase(record);
    }
  };
  const settings = { seed, database: hegel.Database.disabled, suppressHealthCheck: [hegel.HealthCheck.TooSlow], reportMultipleFailures: false };
  try {
    // Isolate each dimension before combinations so an invalid field cannot hide
    // another out-of-range input being accepted.
    for (const field of suite.fields) {
      await hegel.testAsync(async (tc) => {
        const input = { ...suite.baseline, [field]: tc.draw(generators[field]) };
        tc.note(JSON.stringify(input));
        await verify(input, `hegel:${field}`);
      }, { ...settings, testCases: suite.casesPerField ?? 16 });
    }
    await hegel.testAsync(async (tc) => {
      const input = Object.fromEntries(suite.fields.map((field) => [field, tc.draw(generators[field])]));
      tc.note(JSON.stringify(input));
      await verify(input, 'hegel:mixed');
    }, { ...settings, testCases: cases ?? suite.cases ?? 12 });
    if (suite.boundaries !== false) {
      for (const field of suite.fields) for (const value of boundaryValues(fields[field], suite.baseline[field])) {
        await verify({ ...suite.baseline, [field]: value }, `boundary:${field}`);
      }
    }
    for (const example of suite.examples || []) await verify(example, 'model-example');
    return { status: 'PASS', attempts: attempts.length };
  } catch (error) {
    const failure = infrastructureError || error;
    failure.code ||= lastFailure?.status === 'FAIL' ? 'TEST_FAILURE' : 'INCONCLUSIVE';
    failure.failingCase = lastFailure;
    failure.seed = seed;
    throw failure;
  }
}
