import { readFile } from 'node:fs/promises';
import { resolveValue, validateInput } from './declarative.mjs';

const fail = (message) => { throw new Error(`Invalid test model: ${message}`); };
const object = (value) => value && typeof value === 'object' && !Array.isArray(value) && [Object.prototype, null].includes(Object.getPrototypeOf(value));
const text = (value) => typeof value === 'string' && value.trim();
const own = (value, key) => Object.hasOwn(value, key);
const reserved = new Set(['constructor', 'prototype', '__proto__']);
const expressionOps = new Set(['var', 'multiply', 'add', 'trim', 'number']);
const actionKeys = {
  click: ['type', 'target'], fill: ['type', 'target', 'value'], select: ['type', 'target', 'value'],
  check: ['type', 'target', 'value'], press: ['type', 'target', 'key'], navigate: ['type', 'url'],
};
const assertionKeys = {
  visible: ['target'], absent: ['target'], text: ['contains'], notText: ['contains'], value: ['target', 'equals'], count: ['target', 'equals'],
};

function keys(value, allowed, label) {
  if (!object(value)) fail(`${label} must be a JSON object.`);
  for (const key of Object.keys(value)) {
    if (reserved.has(key) || !allowed.includes(key)) fail(`${label}: unsupported field ${key}.`);
  }
}
function identifier(key, label) {
  if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(key) || reserved.has(key)) fail(`${label}: unsafe key ${key}.`);
}
function scalar(value, label) {
  if (value !== null && !['string', 'number', 'boolean'].includes(typeof value)) fail(`${label} must be a JSON scalar.`);
  if (typeof value === 'number' && !Number.isFinite(value)) fail(`${label} must be finite.`);
  if (typeof value === 'string' && value.length > 1000) fail(`${label} exceeds the 1000-character input limit.`);
}
function variable(path, label, scope) {
  if (typeof path !== 'string' || path.length > 256 || path.split('.').some((part) => !/^[A-Za-z0-9_-]+$/.test(part) || reserved.has(part))) fail(`${label}: unsafe variable path ${String(path)}.`);
  const parts = path.split('.');
  if (parts[0] === 'baseUrl' && parts.length === 1) return;
  if (parts[0] === 'graph' && parts.length === 2 && parts[1] === 'raw') return; // GraphWalker exposes its native data as a diagnostic string.
  if (!['input', 'derived'].includes(parts[0]) || parts.length !== 2 || !scope[parts[0]].has(parts[1])) fail(`${label}: unknown variable ${path}.`);
}
function expression(value, label, scope, depth = 0) {
  if (depth > 32) fail(`${label}: expression is too deeply nested.`);
  if (typeof value === 'string') {
    if (value.length > 16384) fail(`${label}: string is too long.`);
    const remaining = value.replace(/\{\{\s*([^{}]+?)\s*\}\}/g, (_, path) => { variable(path.trim(), label, scope); return ''; });
    if (remaining.includes('{{') || remaining.includes('}}')) fail(`${label}: malformed variable template.`);
    return;
  }
  if (value === null || typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value))) return;
  if (!object(value) || Object.keys(value).length !== 1 || !expressionOps.has(Object.keys(value)[0])) fail(`${label}: unsupported expression; use var, multiply, add, trim or number.`);
  const [operator] = Object.keys(value);
  const operand = value[operator];
  if (operator === 'var') { variable(operand, label, scope); return; }
  if (['add', 'multiply'].includes(operator)) {
    if (!Array.isArray(operand) || operand.length < 1 || operand.length > 64) fail(`${label}: ${operator} needs 1–64 operands.`);
    operand.forEach((item) => expression(item, label, scope, depth + 1));
  } else expression(operand, label, scope, depth + 1);
  // Constant expressions can be checked completely during model loading.
  if (!JSON.stringify(value).includes('"var"') && !JSON.stringify(value).includes('{{')) {
    try { resolveValue(value); } catch (error) { fail(`${label}: ${error.message}`); }
  }
}
function target(value, label, scope, assertion = false) {
  keys(value, assertion ? ['role', 'name'] : ['role', 'name', 'intent'], label);
  for (const [key, item] of Object.entries(value)) {
    if (!text(item)) fail(`${label}.${key} must be a nonempty string.`);
    expression(item, `${label}.${key}`, scope);
  }
  if (assertion && !text(value.role)) fail(`${label} requires an accessibility role.`);
  if (!assertion && !text(value.name) && !text(value.intent)) fail(`${label} needs an accessible name or intent.`);
}
function actions(value, label, scope) {
  if (!Array.isArray(value) || !value.length || value.length > 200) fail(`${label} must contain 1–200 actions.`);
  for (const [index, action] of value.entries()) {
    const position = `${label}[${index}]`;
    if (!object(action) || !own(actionKeys, action.type)) fail(`${position}: unsupported action ${action?.type}.`);
    keys(action, actionKeys[action.type], position);
    if (action.type === 'navigate') {
      if (!text(action.url)) fail(`${position}: navigate needs url.`);
      expression(action.url, `${position}.url`, scope);
      const url = action.url.replace(/\{\{[^{}]+\}\}/g, 'model-value');
      if (/^[a-z][a-z0-9+.-]*:/i.test(url) && !/^https?:/i.test(url)) fail(`${position}: navigation only supports HTTP(S).`);
    } else if (action.type !== 'press' || action.target !== undefined) target(action.target, `${position}.target`, scope);
    if (['fill', 'select', 'check'].includes(action.type)) {
      if (!own(action, 'value')) fail(`${position}: ${action.type} needs value.`);
      if (action.type === 'select' && Array.isArray(action.value)) {
        if (!action.value.length || action.value.length > 100) fail(`${position}: select needs 1–100 values.`);
        action.value.forEach((item) => expression(item, `${position}.value`, scope));
      } else expression(action.value, `${position}.value`, scope);
      if (action.type === 'check' && typeof action.value !== 'boolean' && !object(action.value) && !(typeof action.value === 'string' && /^\{\{[^{}]+\}\}$/.test(action.value))) fail(`${position}: check needs a boolean or variable expression.`);
    }
    if (action.type === 'press') {
      if (!text(action.key)) fail(`${position}: press needs key.`);
      expression(action.key, `${position}.key`, scope);
    }
  }
}
function assertions(value, label, scope) {
  if (!Array.isArray(value) || value.length > 200) fail(`${label} must be an array of at most 200 assertions.`);
  for (const [index, assertion] of value.entries()) {
    const position = `${label}[${index}]`;
    if (!object(assertion) || !own(assertionKeys, assertion.type)) fail(`${position}: unsupported assertion ${assertion?.type}.`);
    keys(assertion, ['type', 'when', 'description', ...assertionKeys[assertion.type]], position);
    if (own(assertion, 'description')) {
      if (!text(assertion.description)) fail(`${position}.description must be nonempty.`);
      expression(assertion.description, position, scope);
    }
    if (own(assertion, 'when')) {
      keys(assertion.when, ['exists'], `${position}.when`);
      variable(assertion.when.exists, `${position}.when.exists`, scope);
    }
    if (['visible', 'absent', 'value', 'count'].includes(assertion.type)) target(assertion.target, `${position}.target`, scope, true);
    if (['text', 'notText'].includes(assertion.type)) {
      if (!text(assertion.contains)) fail(`${position}: text assertions need nonempty contains.`);
      expression(assertion.contains, `${position}.contains`, scope);
    }
    if (['value', 'count'].includes(assertion.type)) {
      if (!own(assertion, 'equals')) fail(`${position}: ${assertion.type} needs equals.`);
      expression(assertion.equals, `${position}.equals`, scope);
      if (assertion.equals === null) fail(`${position}: equals cannot be null.`);
      if (assertion.type === 'count' && typeof assertion.equals === 'number' && (!Number.isInteger(assertion.equals) || assertion.equals < 0)) fail(`${position}: count must be a nonnegative integer.`);
      if (assertion.type === 'count' && typeof assertion.equals === 'string' && !assertion.equals.includes('{{')) fail(`${position}: count requires an integer or expression.`);
      if (assertion.type === 'count' && typeof assertion.equals === 'boolean') fail(`${position}: count requires an integer or expression.`);
    }
  }
}
function generator(value, label, depth = 0) {
  if (depth > 8 || !object(value)) fail(`${label}: invalid or overly nested generator.`);
  const shapes = { integer: ['min', 'max'], number: ['min', 'max'], sample: ['values'], text: ['minLength', 'maxLength'], oneOf: ['generators'] };
  if (!own(shapes, value.type)) fail(`${label}: unsupported generator ${value.type}.`);
  keys(value, ['type', ...shapes[value.type]], label);
  if (value.type === 'integer' || value.type === 'number') {
    if (![value.min, value.max].every(Number.isFinite) || value.min > value.max || value.max - value.min > 1e6 || Math.max(Math.abs(value.min), Math.abs(value.max)) > Number.MAX_SAFE_INTEGER) fail(`${label}: numeric generator needs finite ordered min/max, with range at most 1000000.`);
    if (value.type === 'integer' && ![value.min, value.max].every(Number.isSafeInteger)) fail(`${label}: integer bounds must be safe integers.`);
  } else if (value.type === 'text') {
    const min = value.minLength ?? 0, max = value.maxLength ?? 100;
    if (![min, max].every(Number.isInteger) || min < 0 || max < min || max > 1000) fail(`${label}: text length bounds must be integers from 0 to 1000.`);
  } else if (value.type === 'sample') {
    if (!Array.isArray(value.values) || !value.values.length || value.values.length > 1000) fail(`${label}: sample needs 1–1000 values.`);
    value.values.forEach((item) => scalar(item, label));
  } else {
    if (!Array.isArray(value.generators) || !value.generators.length || value.generators.length > 20) fail(`${label}: oneOf needs 1–20 generators.`);
    value.generators.forEach((item) => generator(item, label, depth + 1));
  }
}

export function validateModel(document) {
  if (!object(document) || !Array.isArray(document.models) || document.models.length !== 1) fail('Supply exactly one model per file.');
  const model = document.models[0];
  if (!object(model)) fail('Model must be an object.');
  const config = model.properties?.test;
  keys(config, ['version', 'startUrl', 'demo', 'fields', 'derived', 'properties', 'coverage', 'checkTimeoutMs'], 'model.properties.test');
  if (config.version !== 1) fail('model.properties.test.version must be 1.');
  if (!text(config.startUrl)) fail('model.properties.test.startUrl is required.');
  if (own(config, 'checkTimeoutMs') && (!Number.isInteger(config.checkTimeoutMs) || config.checkTimeoutMs < 0 || config.checkTimeoutMs > 30000)) fail('checkTimeoutMs must be an integer from 0 to 30000.');
  if (!Array.isArray(model.vertices) || !model.vertices.length || !Array.isArray(model.edges) || !model.edges.length) fail('vertices and edges are required.');
  if (model.vertices.length + model.edges.length > 10000) fail('Models are limited to 10000 graph elements.');
  const fields = config.fields ?? {}, derived = config.derived ?? {}, suites = config.properties ?? {};
  if (!object(fields) || !object(derived) || !object(suites)) fail('fields, derived and properties must be objects.');
  for (const key of Object.keys(fields)) identifier(key, 'Field');
  for (const key of Object.keys(derived)) identifier(key, 'Derived value');
  for (const key of Object.keys(suites)) identifier(key, 'Property suite');
  const scope = { input: new Set(Object.keys(fields)), derived: new Set(Object.keys(derived)) };
  expression(config.startUrl, 'startUrl', scope);
  if (own(config, 'demo')) {
    keys(config.demo, ['bugQuery'], 'demo');
    if (own(config.demo, 'bugQuery') && !text(config.demo.bugQuery)) fail('demo.bugQuery must be a nonempty query string.');
  }
  const earlierDerived = new Set();
  for (const [key, value] of Object.entries(derived)) {
    expression(value, `derived.${key}`, { ...scope, derived: earlierDerived });
    earlierDerived.add(key);
  }
  const ids = new Set(), names = new Set();
  for (const item of [...model.vertices, ...model.edges]) {
    if (!object(item) || !text(item.id) || !text(item.name) || ids.has(item.id) || names.has(item.name)) fail('Every id and name must be nonempty and unique.');
    ids.add(item.id); names.add(item.name);
  }
  const states = new Map(model.vertices.map((vertex) => [vertex.id, vertex]));
  if (!states.has(model.startElementId)) fail('This runner starts from a vertex; set startElementId to its id.');
  for (const vertex of model.vertices) {
    const test = vertex.properties?.test;
    keys(test, ['description', 'expectation', 'match', 'assertions'], `${vertex.id}.test`);
    if (!text(test.description) || !text(test.expectation)) fail(`${vertex.id} needs test.description and test.expectation.`);
    if (test.description.includes('{{') || test.description.includes('}}')) fail(`${vertex.id}.description must be static text without templates; use expectation for dynamic values.`);
    expression(test.description, `${vertex.id}.description`, scope);
    expression(test.expectation, `${vertex.id}.expectation`, scope);
    assertions(test.match ?? [], `${vertex.id}.match`, scope);
    assertions(test.assertions ?? [], `${vertex.id}.assertions`, scope);
  }
  for (const [key, field] of Object.entries(fields)) {
    keys(field, ['target', 'constraints', 'generator', 'error'], `field ${key}`);
    target(field.target, `field ${key}.target`, scope);
    if (!object(field.constraints) || !['string', 'integer', 'number', 'email'].includes(field.constraints.type)) fail(`field ${key} needs supported constraints.type.`);
    const numeric = ['integer', 'number'].includes(field.constraints.type);
    const incompatible = numeric ? ['minLength', 'maxLength'] : ['min', 'max'];
    if (incompatible.some((rule) => own(field.constraints, rule))) fail(`field ${key}: constraint does not apply to ${field.constraints.type}.`);
    for (const rule of ['minLength', 'maxLength']) if (field.constraints[rule] > 1000) fail(`field ${key}: lengths cannot exceed 1000.`);
    if (!text(field.error)) fail(`field ${key} needs the expected validation error text.`);
    if (own(field, 'generator')) generator(field.generator, `field ${key}.generator`);
  }
  try { validateInput(fields, {}); } catch (error) { fail(error.message); }
  const edges = new Map(model.edges.map((edge) => [edge.id, edge]));
  for (const edge of model.edges) {
    if (!states.has(edge.sourceVertexId) || !states.has(edge.targetVertexId)) fail(`${edge.id} links to an unknown vertex.`);
    const test = edge.properties?.test;
    keys(test, ['actions', 'input', 'property', 'assertions', 'clearInput'], `${edge.id}.test`);
    if (!own(test, 'actions') && !text(test.property)) fail(`${edge.id} needs actions or a property suite.`);
    if (own(test, 'actions')) actions(test.actions, `${edge.id}.actions`, scope);
    if (own(test, 'property') && (!text(test.property) || !own(suites, test.property))) fail(`${edge.id} refers to an unknown property suite.`);
    if (test.property && own(test, 'actions')) fail(`${edge.id}: property suites cannot also declare ignored actions.`);
    if (test.property && edge.sourceVertexId !== edge.targetVertexId) fail(`${edge.id}: property suites must return to the source state (self-loop).`);
    if (own(test, 'clearInput') && typeof test.clearInput !== 'boolean') fail(`${edge.id}.clearInput must be boolean.`);
    if (own(test, 'input')) {
      if (!object(test.input)) fail(`${edge.id}.input must be an object.`);
      for (const [key, value] of Object.entries(test.input)) {
        if (!own(fields, key)) fail(`${edge.id}: unknown input field ${key}.`);
        expression(value, `${edge.id}.input.${key}`, scope);
      }
    }
    assertions(test.assertions ?? [], `${edge.id}.assertions`, scope);
  }
  for (const [name, suite] of Object.entries(suites)) {
    keys(suite, ['fields', 'baseline', 'resetEdges', 'submit', 'validState', 'invalidState', 'cases', 'casesPerField', 'boundaries', 'examples'], `property ${name}`);
    if (!Array.isArray(suite.fields) || !suite.fields.length || suite.fields.some((key) => !own(fields, key)) || new Set(suite.fields).size !== suite.fields.length) fail(`${name}: unknown, duplicate or empty fields.`);
    const caseInput = (input, label) => {
      if (!object(input) || suite.fields.some((key) => !own(input, key)) || Object.keys(input).some((key) => !suite.fields.includes(key))) fail(`${label} must supply exactly the suite fields.`);
      for (const value of Object.values(input)) scalar(value, label);
    };
    caseInput(suite.baseline, `${name}.baseline`);
    const scopedFields = Object.fromEntries(suite.fields.map((key) => [key, fields[key]]));
    if (!validateInput(scopedFields, suite.baseline).valid) fail(`${name}.baseline must satisfy its field constraints.`);
    if (own(suite, 'examples')) {
      if (!Array.isArray(suite.examples) || suite.examples.length > 200) fail(`${name}.examples must be an array of at most 200 inputs.`);
      suite.examples.forEach((input) => caseInput(input, `${name}.examples`));
    }
    if (!states.has(suite.validState) || !states.has(suite.invalidState) || suite.validState === suite.invalidState) fail(`${name}: validState/invalidState must be distinct model vertices.`);
    for (const state of [suite.validState, suite.invalidState]) {
      const match = states.get(state).properties.test.match;
      if (!match?.length || match.every((assertion) => own(assertion, 'when'))) fail(`${name}: property outcome states need unconditional match assertions for shrinking.`);
    }
    actions(suite.submit, `${name}.submit`, scope);
    if (!Array.isArray(suite.resetEdges) || suite.resetEdges.length > 200) fail(`${name}.resetEdges must be an explicit model path of at most 200 edges from the initial state.`);
    let current = model.startElementId;
    for (const id of suite.resetEdges) {
      const edge = edges.get(id);
      if (!edge || edge.sourceVertexId !== current || edge.properties.test.property) fail(`${name}: resetEdges must be a connected path without property suites.`);
      if (edge.guard !== undefined && edge.guard !== null && edge.guard !== '') fail(`${name}: resetEdges cannot include guarded edges; browser resets do not replay native GraphWalker guards.`);
      current = edge.targetVertexId;
    }
    const suiteEdges = model.edges.filter((edge) => edge.properties.test.property === name);
    if (!suiteEdges.length) fail(`${name}: property suite is not referenced by a graph edge.`);
    for (const edge of suiteEdges) if (edge.sourceVertexId !== current) fail(`${name}: resetEdges does not reach property source state.`);
    for (const key of ['cases', 'casesPerField']) if (own(suite, key) && (!Number.isInteger(suite[key]) || suite[key] < 1 || suite[key] > 200)) fail(`${name}.${key} must be 1–200.`);
    if (own(suite, 'boundaries') && typeof suite.boundaries !== 'boolean') fail(`${name}.boundaries must be boolean.`);
  }
  if (own(config, 'coverage')) keys(config.coverage, ['edges', 'vertices', 'requirements'], 'coverage');
  const coverage = { edges: 100, vertices: 100, ...(config.coverage ?? {}) };
  for (const value of [coverage.edges, coverage.vertices]) if (!Number.isFinite(value) || value < 0 || value > 100) fail('Coverage percentages must be 0–100.');
  if (own(coverage, 'requirements') && (!Array.isArray(coverage.requirements) || coverage.requirements.some((value) => !text(value)) || new Set(coverage.requirements).size !== coverage.requirements.length)) fail('coverage.requirements must be a list of unique requirement IDs.');
  return { document, model, config, fields, suites, states, edges, coverage };
}

export async function loadModel(path) { return validateModel(JSON.parse(await readFile(path, 'utf8'))); }
