// The framework interprets JSON only. No model strings are evaluated as JavaScript.
const RESERVED = new Set(['__proto__', 'prototype', 'constructor']);
const OPERATORS = new Set(['var', 'multiply', 'add', 'trim', 'number']);
const MAX_DEPTH = 32;
const MAX_ITEMS = 10000;
const MAX_STRING = 16384;
const DECIMAL = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
const normalized = (value) => String(value ?? '').replace(/\s+/g, ' ').trim().toLowerCase();
const own = (value, key) => value !== null && typeof value === 'object' && Object.hasOwn(value, key);

function plainObject(value, description = 'Value') {
  if (!value || typeof value !== 'object' || Array.isArray(value) || ![Object.prototype, null].includes(Object.getPrototypeOf(value))) {
    throw new Error(`${description} must be a plain JSON object.`);
  }
  for (const key of Object.keys(value)) if (RESERVED.has(key)) throw new Error(`Unsafe JSON key: ${key}`);
}

function lookup(path, context, { optional = false } = {}) {
  if (typeof path !== 'string' || !path || path.length > 256) throw new Error('Variable paths must be nonempty strings of at most 256 characters.');
  const parts = path.split('.');
  if (parts.some((part) => !/^[A-Za-z0-9_-]+$/.test(part) || RESERVED.has(part))) throw new Error(`Unsafe variable path: ${path}`);
  let result = context;
  for (const part of parts) {
    if (!own(result, part)) {
      if (optional) return { exists: false };
      throw new Error(`Missing model variable: ${path}`);
    }
    result = result[part];
  }
  if (result === undefined) {
    if (optional) return { exists: false };
    throw new Error(`Missing model variable: ${path}`);
  }
  return optional ? { exists: true, value: result } : result;
}

function finiteNumber(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() && DECIMAL.test(value.trim())) {
    const result = Number(value.trim());
    if (Number.isFinite(result)) return result;
  }
  throw new Error(`Expected a finite decimal number, received ${JSON.stringify(value)}.`);
}

/** Recursively resolve JSON and {{path}} templates; exact templates preserve type.
 * Expressions use exactly one of var, multiply, add, trim or number as their key.
 * Other plain JSON objects are data; an `op` discriminator is deliberately unsupported.
 * Resolution has depth, size, string and operand limits, and never evaluates code.
 */
export function resolveValue(value, context = {}) {
  let visited = 0;
  function visit(current, depth, interpret = true) {
    if (++visited > MAX_ITEMS || depth > MAX_DEPTH) throw new Error('Model value exceeds resolution bounds.');
    if (typeof current === 'string') {
      if (current.length > MAX_STRING) throw new Error('Model string exceeds resolution bounds.');
      if (!interpret) return current;
      const exact = current.match(/^\{\{\s*([^{}]+?)\s*\}\}$/);
      if (exact) return visit(lookup(exact[1].trim(), context), depth + 1, false);
      const resolved = current.replace(/\{\{\s*([^{}]+?)\s*\}\}/g, (_, path) => {
        const result = lookup(path.trim(), context);
        if (result === null || !['string', 'number', 'boolean'].includes(typeof result)) throw new Error(`Cannot interpolate a non-scalar variable: ${path}`);
        return String(result);
      });
      if (resolved.length > MAX_STRING) throw new Error('Resolved string exceeds resolution bounds.');
      return resolved;
    }
    if (current === null || typeof current === 'boolean') return current;
    if (typeof current === 'number') return finiteNumber(current);
    if (Array.isArray(current)) return current.map((item) => visit(item, depth + 1, interpret));
    plainObject(current);
    const keys = Object.keys(current);
    const operators = keys.filter((key) => OPERATORS.has(key));
    if (interpret && operators.length) {
      if (keys.length !== 1) throw new Error('A model expression must contain exactly one operator.');
      const operator = operators[0];
      const operand = current[operator];
      if (operator === 'var') return visit(lookup(operand, context), depth + 1, false);
      if (operator === 'multiply' || operator === 'add') {
        if (!Array.isArray(operand) || operand.length < 1 || operand.length > 64) throw new Error(`${operator} requires between 1 and 64 operands.`);
        const numbers = operand.map((item) => finiteNumber(visit(item, depth + 1)));
        return finiteNumber(numbers.reduce((result, number) => operator === 'multiply' ? result * number : result + number, operator === 'multiply' ? 1 : 0));
      }
      const result = visit(operand, depth + 1);
      if (operator === 'number') return finiteNumber(result);
      if (!['string', 'number'].includes(typeof result)) throw new Error('trim requires a string or number.');
      return String(result).trim();
    }
    if (interpret && own(current, 'op')) throw new Error(`Unknown model operator: ${String(current.op)}`);
    return Object.fromEntries(keys.map((key) => [key, visit(current[key], depth + 1, interpret)]));
  }
  return visit(value, 0);
}

// Inspect definitions, never their input values. Validate independent constant
// subexpressions even when another dependency is currently unavailable.
function derivedDependencies(expression) {
  let visited = 0;
  function inspect(value, depth = 0) {
    if (++visited > MAX_ITEMS || depth > MAX_DEPTH) throw new Error('Model value exceeds resolution bounds.');
    const paths = new Set();
    const include = (path) => {
      lookup(path, {}, { optional: true }); // Validate paths even when their inputs are absent.
      paths.add(path);
    };
    const merge = (nested) => { for (const path of nested) paths.add(path); };
    if (typeof value === 'string') {
      if (value.length > MAX_STRING) throw new Error('Model string exceeds resolution bounds.');
      for (const match of value.matchAll(/\{\{\s*([^{}]+?)\s*\}\}/g)) include(match[1].trim());
    } else if (Array.isArray(value)) {
      for (const item of value) merge(inspect(item, depth + 1));
    } else if (value !== null && typeof value === 'object') {
      plainObject(value);
      const keys = Object.keys(value);
      const operators = keys.filter((key) => OPERATORS.has(key));
      if (operators.length) {
        if (keys.length !== 1) throw new Error('A model expression must contain exactly one operator.');
        const operator = operators[0], operand = value[operator];
        if (operator === 'var') include(operand);
        else if (operator === 'multiply' || operator === 'add') {
          if (!Array.isArray(operand) || operand.length < 1 || operand.length > 64) throw new Error(`${operator} requires between 1 and 64 operands.`);
          for (const item of operand) {
            const dependencies = inspect(item, depth + 1);
            if (!dependencies.size) finiteNumber(resolveValue(item));
            merge(dependencies);
          }
        } else merge(inspect(operand, depth + 1));
      } else {
        if (own(value, 'op')) throw new Error(`Unknown model operator: ${String(value.op)}`);
        for (const item of Object.values(value)) merge(inspect(item, depth + 1));
      }
    }
    if (!paths.size) resolveValue(value);
    return paths;
  }
  return inspect(expression);
}

/** Derived values resolve in declaration order and can refer to earlier values.
 * With fields supplied, skip only definitions whose own declared input fields
 * are missing/invalid, plus values that depend on those skipped definitions.
 * The two-argument form remains strict: missing variables always throw.
 */
export function computeDerived(definitions = {}, context = {}, fields) {
  plainObject(definitions, 'Derived definitions');
  const derived = { ...(context.derived ?? {}) };
  if (fields === undefined) {
    for (const [key, expression] of Object.entries(definitions)) derived[key] = resolveValue(expression, { ...context, derived });
    return derived;
  }
  // Eager schema validation prevents an unrelated malformed field definition
  // from being hidden behind unavailable input in this particular test case.
  validateInput(fields, {});
  const skipped = new Set();
  const availableInputs = new Map();
  // Never preserve an earlier result for a definition whose inputs changed.
  for (const key of Object.keys(definitions)) delete derived[key];
  for (const [key, expression] of Object.entries(definitions)) {
    let ready = true;
    for (const path of derivedDependencies(expression)) {
      const [scope, field, ...tail] = path.split('.');
      if (scope === 'input') {
        if (tail.length || !own(fields, field)) throw new Error(`Unknown derived input field: ${path}`);
        if (!availableInputs.has(field)) {
          const present = lookup(path, context, { optional: true }).exists;
          const valid = present && validateInput({ [field]: fields[field] }, context.input).valid;
          availableInputs.set(field, valid);
        }
        if (!availableInputs.get(field)) ready = false;
      } else if (scope === 'derived' && !tail.length && skipped.has(field)) {
        ready = false;
      } else {
        // Unknown variables and forward references are model errors, not an
        // unavailable form dependency. Do not turn them into skipped values.
        lookup(path, { ...context, derived });
      }
    }
    if (ready) derived[key] = resolveValue(expression, { ...context, derived });
    else skipped.add(key);
  }
  return derived;
}

function matchingNodes(target, evidence, context) {
  plainObject(target, 'Assertion target');
  const resolved = resolveValue(target, context);
  if (typeof resolved.role !== 'string' || !resolved.role.trim()) throw new Error('Assertion targets require an accessibility role.');
  if (own(resolved, 'name') && typeof resolved.name !== 'string') throw new Error('Target accessible names must be strings.');
  if (!Array.isArray(evidence?.nodes)) throw new Error('Browser evidence must contain accessible nodes.');
  return evidence.nodes.filter((node) => normalized(node.role) === normalized(resolved.role)
    && (!own(resolved, 'name') || normalized(node.name) === normalized(resolved.name)));
}

function assertionEnabled(assertion, context) {
  if (!own(assertion, 'when')) return true;
  plainObject(assertion.when, 'Assertion condition');
  if (Object.keys(assertion.when).length !== 1 || !own(assertion.when, 'exists')) throw new Error('Assertion conditions support only {exists: "variable.path"}.');
  return lookup(assertion.when.exists, context, { optional: true }).exists;
}

/** Evaluate independent model requirements against the browser's visible evidence.
 * Accessible-name matching ignores case and repeated whitespace; text/value checks
 * preserve case. Value assertions require a unique node and compare form strings to
 * scalar expected values. An absent optional context gate produces no check.
 */
export function evaluateAssertions(assertions, evidence, context = {}) {
  if (!Array.isArray(assertions)) throw new Error('Assertions must be an array.');
  const checks = [];
  const types = new Set(['visible', 'absent', 'text', 'notText', 'value', 'count']);
  for (const assertion of assertions) {
    plainObject(assertion, 'Assertion');
    // Unknown assertions are errors even when their optional context is unavailable.
    if (!types.has(assertion.type)) throw new Error(`Unknown assertion type: ${String(assertion.type)}`);
    if (!assertionEnabled(assertion, context)) continue;
    let passed = false;
    let description;
    if (assertion.type === 'text' || assertion.type === 'notText') {
      const expected = resolveValue(assertion.contains, context);
      if (typeof expected !== 'string' || !expected.trim()) throw new Error('Text assertions require a nonempty contains string.');
      if (typeof evidence?.text !== 'string') throw new Error('Browser evidence must contain visible text.');
      const found = evidence.text.replace(/\s+/g, ' ').includes(expected.replace(/\s+/g, ' '));
      passed = assertion.type === 'text' ? found : !found;
      description = `${assertion.type === 'text' ? 'Text contains' : 'Text excludes'} ${JSON.stringify(expected)}`;
    } else {
      const nodes = matchingNodes(assertion.target, evidence, context);
      const target = resolveValue(assertion.target, context);
      const label = `${target.role}${own(target, 'name') ? ` ${JSON.stringify(target.name)}` : ''}`;
      if (assertion.type === 'visible') { passed = nodes.length > 0; description = `Visible ${label}`; }
      if (assertion.type === 'absent') { passed = nodes.length === 0; description = `Absent ${label}`; }
      if (assertion.type === 'count') {
        const expected = resolveValue(assertion.equals, context);
        if (!Number.isInteger(expected) || expected < 0) throw new Error('Count assertions require a nonnegative integer.');
        passed = nodes.length === expected;
        description = `${label} count equals ${expected}`;
      }
      if (assertion.type === 'value') {
        const expected = resolveValue(assertion.equals, context);
        if (expected === null || !['string', 'number', 'boolean'].includes(typeof expected)) throw new Error('Value assertions require a scalar expected value.');
        passed = nodes.length === 1 && own(nodes[0], 'value') && String(nodes[0].value) === String(expected);
        description = `${label} value equals ${JSON.stringify(expected)}${nodes.length > 1 ? ' (ambiguous target)' : ''}`;
      }
    }
    checks.push({ description: assertion.description ? String(resolveValue(assertion.description, context)) : description, passed });
  }
  return checks;
}

/** Return one uniquely matching state. Missing, empty or ambiguous matches fail closed. */
export function classifyState(states, evidence, context = {}) {
  if (!Array.isArray(states)) throw new Error('States must be an array.');
  const matches = states.filter((state) => {
    if (typeof state.id !== 'string' || !state.id) throw new Error('States require an id.');
    const checks = evaluateAssertions(state.match ?? [], evidence, context);
    return checks.length > 0 && checks.every((check) => check.passed);
  });
  return matches.length === 1 ? matches[0].id : '__unknown__';
}

const CONSTRAINTS = new Set(['type', 'required', 'trim', 'minLength', 'maxLength', 'min', 'max', 'pattern', 'message']);

/** Validate against model-owned requirements, independently of the web application.
 * Empty optional fields are allowed. Required checks apply after optional trimming.
 * String lengths count Unicode code points; numeric strings must be finite decimals.
 * Integer means an integral numeric value (e.g. "2.0" qualifies); add a pattern for
 * stricter textual formats. Email requires one @, no whitespace and a dotted domain.
 * Patterns are JavaScript regular expressions tested against the full field value.
 */
export function validateInput(fields, input) {
  plainObject(fields, 'Fields');
  plainObject(input, 'Input');
  const violations = [];
  for (const [field, definition] of Object.entries(fields)) {
    plainObject(definition, `Field ${field}`);
    const constraints = definition.constraints ?? {};
    plainObject(constraints, `Constraints for ${field}`);
    for (const rule of Object.keys(constraints)) if (!CONSTRAINTS.has(rule)) throw new Error(`Unknown input constraint: ${rule}`);
    const type = constraints.type ?? 'string';
    if (!['string', 'email', 'integer', 'number'].includes(type)) throw new Error(`Unknown input type: ${type}`);
    for (const rule of ['required', 'trim']) if (own(constraints, rule) && typeof constraints[rule] !== 'boolean') throw new Error(`${rule} must be boolean.`);
    for (const rule of ['minLength', 'maxLength']) if (own(constraints, rule) && (!Number.isInteger(constraints[rule]) || constraints[rule] < 0)) throw new Error(`${rule} must be a nonnegative integer.`);
    for (const rule of ['min', 'max']) if (own(constraints, rule) && (typeof constraints[rule] !== 'number' || !Number.isFinite(constraints[rule]))) throw new Error(`${rule} must be a finite number.`);
    if (constraints.minLength > constraints.maxLength || constraints.min > constraints.max) throw new Error(`Contradictory constraints for ${field}.`);
    let pattern;
    if (own(constraints, 'pattern')) {
      if (typeof constraints.pattern !== 'string' || constraints.pattern.length > 256) throw new Error('Input patterns must be strings of at most 256 characters.');
      // Patterns come from trusted local models. Avoid common nested-repeat mistakes.
      if (/\([^)]*[+*][^)]*\)[+*{]/.test(constraints.pattern)) throw new Error('Nested repeated input patterns are unsupported.');
      pattern = new RegExp(`^(?:${constraints.pattern})$`, 'u');
    }
    const violate = (rule) => violations.push({ field, rule, ...(constraints.message ? { message: constraints.message } : {}) });
    let value = input[field];
    if (typeof value === 'string') {
      if (value.length > MAX_STRING) { violate('maxInputLength'); continue; }
      if (constraints.trim) value = value.trim();
    }
    if (value === undefined || value === null || value === '') {
      if (constraints.required) violate('required');
      continue;
    }
    if (type === 'string' || type === 'email') {
      if (typeof value !== 'string') { violate('type'); continue; }
      const length = [...value].length;
      if (own(constraints, 'minLength') && length < constraints.minLength) violate('minLength');
      if (own(constraints, 'maxLength') && length > constraints.maxLength) violate('maxLength');
      if (type === 'email') {
        const parts = value.split('@');
        const domain = (parts[1] ?? '').split('.');
        if (parts.length !== 2 || !parts[0] || /\s/.test(value) || domain.length < 2 || domain.some((part) => !part)) violate('email');
      }
    } else {
      let number;
      try { number = finiteNumber(value); } catch { violate('type'); continue; }
      if (type === 'integer' && !Number.isInteger(number)) violate('integer');
      if (own(constraints, 'min') && number < constraints.min) violate('min');
      if (own(constraints, 'max') && number > constraints.max) violate('max');
    }
    if (pattern && !pattern.test(String(value))) violate('pattern');
  }
  return { valid: violations.length === 0, violations };
}
