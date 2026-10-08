import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { writeReport } from '../src/report.mjs';

function fixture(directory, status = 'PASS') {
  return {
    model: 'Example model', status, provider: 'offline', seed: 42,
    startedAt: '2026-10-09T00:00:00Z', finishedAt: '2026-10-09T00:00:01Z',
    modelHash: 'abc123', startUrl: 'http://localhost:4173/',
    coverage: {
      edges: { visited: 2, total: 3, percent: 66.67, required: 50, ids: ['open', 'back'] },
      vertices: { visited: 2, total: 2, percent: 100, required: 100, ids: ['first', 'second'] },
      requirements: { visited: 1, total: 2, ids: ['FORM-1'], missing: ['FORM-2'] },
      properties: { completed: ['bounds'], total: 1 },
    },
    decisions: { calls: 0, cacheHits: 0, inputTokens: 0, outputTokens: 0, totalTokens: 0 },
    steps: [
      { id: 'open', name: 'Open form', type: 'edge', status: 'PASS' },
      { id: 'second', name: 'Form state', type: 'vertex', status: 'INCONCLUSIVE', observedState: '__unknown__', source: 'deterministic', checks: [{ passed: true, description: 'A visible input exists' }], screenshot: join(directory, 'browser', 'form state.png') },
    ],
    formCases: [{ suite: 'bounds', source: 'hegel:amount', status: 'PASS', input: { amount: 2 }, expectedState: 'accepted', observedState: 'accepted' }],
    resolutions: [{ method: 'exact', target: { role: 'button', name: 'Continue' } }],
  };
}

async function render(t, transform = () => {}) {
  const directory = await mkdtemp(join(tmpdir(), 'model-test-report-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const report = fixture(directory);
  transform(report, directory);
  await writeReport(directory, report);
  return { html: await readFile(join(directory, 'report.html'), 'utf8'), saved: JSON.parse(await readFile(join(directory, 'report.json'), 'utf8')), report };
}

test('renders generic verdicts, coverage, actual check sources, usage and saved evidence', async (t) => {
  const { html, saved, report } = await render(t);
  assert.deepEqual(saved, report);
  for (const value of ['Example model', 'Verified coverage', 'Edges', 'Vertices', 'Requirements', 'Property suites', '66.67%', '50%', 'FORM-2', 'Decision usage', 'API requests', 'Input tokens', 'Graph traversal', 'A visible input exists', 'deterministic', 'INCONCLUSIVE']) assert.ok(html.includes(value), value);
  assert.ok(html.includes('Offline verification uses deterministic model assertions. No LLM judgment is used.'));
  assert.ok(html.includes('href="report.json"'));
  assert.ok(html.includes('href="model.json"'));
  assert.ok(html.includes('href="browser/mcp-transcript.json"'));
  assert.ok(html.includes('href="browser/form%20state.png"'));
  assert.ok(!html.includes('href="replay.json"'));
  assert.ok(!html.includes('FIELDNOTES'));
});

test('failure report includes the reduced input, failed checks and replay artifact', async (t) => {
  const { html } = await render(t, (report) => {
    report.status = 'FAIL';
    report.error = 'Upper limit was accepted';
    report.reason = 'TEST_FAILURE';
    report.counterexample = { suite: 'bounds', source: 'hegel:amount', status: 'FAIL', input: { amount: 5 }, checks: [{ passed: false, description: 'The upper limit must be enforced' }] };
    report.formCases.push(report.counterexample);
  });
  for (const value of ['Hegel-reduced counterexample', 'Upper limit was accepted', 'TEST_FAILURE', '&quot;amount&quot;: 5', 'The upper limit must be enforced', 'href="replay.json"', '1 pass · 1 fail · 0 inconclusive']) assert.ok(html.includes(value), value);
});

test('uncertainty and boundary failures are not presented as Hegel-minimized bugs', async (t) => {
  const { html } = await render(t, (report) => {
    report.status = 'INCONCLUSIVE';
    report.error = 'Decision call budget exhausted';
    report.counterexample = { suite: 'bounds', source: 'hegel:amount', status: 'INCONCLUSIVE', input: { amount: 2 } };
  });
  assert.ok(html.includes('Interrupted input'));
  assert.ok(!html.includes('<h2>Hegel-reduced counterexample</h2>'));
  const boundary = await render(t, (report) => {
    report.status = 'FAIL';
    report.counterexample = { suite: 'bounds', source: 'boundary:amount', status: 'FAIL', input: { amount: 5 } };
  });
  assert.ok(boundary.html.includes('<h2>Failing input</h2>'));
});

test('escapes model and browser text and excludes screenshot paths outside the report', async (t) => {
  const injection = '<script>alert("unsafe")</script>';
  const { html, saved } = await render(t, (report, directory) => {
    report.model = injection;
    report.error = injection;
    report.startUrl = `javascript:${injection}`;
    report.formCases[0].input = { value: injection };
    report.steps[1].checks[0].description = injection;
    report.steps[1].screenshot = join(directory, '..', 'outside.png');
    report.resolutions[0].target.name = injection;
    report.failureEvidence = { snapshot: injection };
  });
  assert.ok(!html.includes(injection));
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;script&gt;alert(&quot;unsafe&quot;)&lt;/script&gt;'));
  assert.ok(!html.includes('outside.png'));
  assert.equal(saved.model, injection, 'JSON evidence preserves the original observation');
});

test('live configuration without calls does not claim a provider checked the run', async (t) => {
  const { html } = await render(t, (report) => { report.provider = 'openai'; });
  assert.ok(html.includes('No decision API requests were made in this run.'));
  assert.ok(!html.includes('provider was called.'));
  const live = await render(t, (report) => { report.provider = 'openai'; report.decisions.calls = 2; report.decisions.inputTokens = 87; });
  assert.ok(live.html.includes('The configured Decisions-compatible provider was called.'));
  assert.ok(live.html.includes('<strong>87</strong><span>Input tokens</span>'));
});
