import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { Browser } from './browser.mjs';
import { startServer } from './server.mjs';
import { generatePath } from './graphwalker.mjs';
import { judgeState } from './decisions.mjs';
import { observe, checkState } from './oracle.mjs';
import { testFormInputs } from './properties.mjs';
import { writeReport } from './report.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const { values } = parseArgs({ options: {
  offline: { type: 'boolean', default: false },
  bug: { type: 'boolean', default: false },
  headed: { type: 'boolean', default: false },
  model: { type: 'string', default: resolve(root, 'models/booking.json') },
  seed: { type: 'string', default: '42' },
  cases: { type: 'string', default: '12' },
  'max-steps': { type: 'string', default: '150' },
  help: { type: 'boolean', default: false },
} });

if (values.help) {
  console.log('Usage: npm run demo -- [--offline] [--headed] [--bug] [--seed 42] [--cases 12] [--model models/booking.json] [--max-steps 150]');
  process.exit(0);
}

function integer(value, label, min, max) {
  const n = Number(value);
  if (!Number.isSafeInteger(n) || n < min || n > max) throw new Error(`${label} must be an integer from ${min} to ${max}.`);
  return n;
}

async function main() {
  const seed = integer(values.seed, 'seed', 1, 2147483647);
  const cases = integer(values.cases, 'cases', 1, 200);
  const maxSteps = integer(values['max-steps'], 'max-steps', 1, 2000);
  if (!values.offline && !process.env.OPENAI_API_KEY?.trim()) {
    throw new Error('Live mode requires OPENAI_API_KEY. Copy .env.example to .env and set your key, or explicitly run npm run demo:offline.');
  }
  const modelPath = resolve(values.model);
  const document = JSON.parse(await readFile(modelPath, 'utf8'));
  if (document.models?.length !== 1) throw new Error('This small demo supports one model per seed file.');
  const model = document.models[0];
  const mode = values.offline ? 'offline' : 'openai';
  const directory = resolve(root, 'artifacts', `${new Date().toISOString().replace(/[:.]/g, '-')}-${mode}${values.bug ? '-bug' : ''}`);
  await mkdir(directory, { recursive: true });
  const report = { status: 'RUNNING', mode, seed, modelPath, startedAt: new Date().toISOString(),
    coverage: { visited: 0, total: model.edges.length }, steps: [], formCases: [], bugEnabled: values.bug };
  const visited = new Set();
  const photographed = new Set();
  const browser = new Browser({ outputDir: resolve(directory, 'browser'), headed: values.headed });
  let server, booking, previousEdge, step, propertiesRun = false;

  try {
    const path = await generatePath({ modelPath, seed, maxSteps });
    report.plannedPath = path;
    await writeFile(resolve(directory, 'model.json'), JSON.stringify(document, null, 2));
    server = await startServer();
    const url = `${server.url}/${values.bug ? '?bug=seats' : ''}`;
    console.log(`Mode: ${mode}${values.offline ? ' (no OpenAI calls)' : ' (live Decisions API)'} · seed ${seed}`);
    console.log(`GraphWalker planned ${path.length} elements · ${model.edges.length} edges to cover`);
    await browser.start();
    await browser.navigate(url);

    const submit = async (input) => {
      booking = input;
      await browser.fill(input);
      await browser.click('Review booking');
    };
    const valid = { name: 'Ada Lovelace', email: 'ada@example.com', seats: '2' };
    const actions = {
      e_OpenForm: () => browser.click('Book a place'),
      e_ValidateInputs: async () => {
        if (!propertiesRun) {
          console.log('  Hegel: generating and checking form inputs…');
          await testFormInputs({ browser, url, seed, testCases: cases, onCase: (entry) => {
            report.formCases.push(entry);
            console.log(`    ${entry.passed ? 'PASS' : 'FAIL'} ${entry.source || 'hegel'} ${JSON.stringify(entry.input)}`);
          } });
          propertiesRun = true;
          booking = undefined;
        }
      },
      e_InvalidBooking: () => submit({ name: 'A', email: 'not-an-email', seats: '0' }),
      e_CorrectBooking: () => submit({ ...valid }),
      e_ValidBooking: () => submit({ ...valid }),
      e_EditBooking: () => browser.click('Edit details'),
      e_Confirm: () => browser.click('Confirm booking'),
      e_StartAgain: async () => { await browser.click('Start again'); booking = undefined; },
      e_Cancel: () => browser.click('Back to home'),
      e_CancelError: () => browser.click('Back to home'),
    };
    const states = model.vertices.map((vertex) => ({ id: vertex.id, description: vertex.properties?.description }));
    for (const element of path) {
      step = { id: element.id, name: element.name, type: element.type, passed: false };
      report.steps.push(step);
      if (element.type === 'edge') {
        const action = actions[element.name];
        if (!action) throw new Error(`No browser binding for edge ${element.name}. Add it in src/run.mjs.`);
        await action();
        previousEdge = element.name;
        visited.add(element.id);
      } else {
        const vertex = model.vertices.find((item) => item.id === element.id);
        if (!vertex) throw new Error(`Unknown model vertex ${element.id}.`);
        const evidence = await observe(browser);
        step.evidence = evidence;
        step.local = checkState(vertex.id, evidence, { booking, previousEdge });
        let expectation = vertex.properties?.expectation;
        if (booking && ['v_Review', 'v_Confirmed'].includes(vertex.id)) {
          expectation += ` The submitted customer is ${JSON.stringify(booking.name.trim())}, email ${JSON.stringify(booking.email.trim())}, seats ${booking.seats}.`;
          if (vertex.id === 'v_Review') expectation += ` The total is AUD $${Number(booking.seats) * 45}.`;
        }
        if (previousEdge === 'e_EditBooking' && booking) expectation += ` The form must retain ${JSON.stringify(booking)}.`;
        step.decision = values.offline ? step.local : await judgeState({
          states, expectedState: vertex.id, expectation, evidence,
          model: process.env.OPENAI_DECISION_MODEL || 'gpt-6-luna',
        });
        if (!photographed.has(vertex.id) || !step.decision.passed || !step.local.passed) {
          step.screenshot = await browser.screenshot(`${String(report.steps.length).padStart(3, '0')}-${vertex.id}.png`);
          photographed.add(vertex.id);
        }
        if (!step.local.passed || !step.decision.passed) {
          throw new Error(`Checkpoint ${vertex.name} failed. Observed ${step.decision.observedState}; inspect the report for assertions and API probabilities.`);
        }
      }
      step.passed = true;
      console.log(`PASS ${element.name}${step.decision ? ` → ${step.decision.observedState}` : ''}`);
    }
    if (visited.size !== model.edges.length) throw new Error(`Incomplete edge coverage: ${visited.size}/${model.edges.length}.`);
    if (!propertiesRun) throw new Error('The path did not execute the Hegel form property binding.');
    report.status = 'PASS';
  } catch (error) {
    report.status = 'FAIL';
    report.error = error.stack || String(error);
    if (step) step.error = error.message;
    try {
      if (browser.client) {
        report.failureSnapshot = await browser.snapshot();
        report.failureScreenshot = await browser.screenshot('failure.png');
      }
    } catch (captureError) { report.captureError = captureError.message; }
    console.error(`FAIL: ${error.message}`);
    process.exitCode = 1;
  } finally {
    try { await browser.close(); } catch (error) { report.cleanupError = error.message; }
    if (server) await server.close();
    report.finishedAt = new Date().toISOString();
    report.coverage.visited = visited.size;
    report.coverage.edges = [...visited];
    await writeReport(directory, report);
    console.log(`${report.status} · edge coverage ${visited.size}/${model.edges.length} · form attempts ${report.formCases.length}`);
    console.log(`Report: ${resolve(directory, 'report.html')}`);
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1; });
