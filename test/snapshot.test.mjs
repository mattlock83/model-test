import test from 'node:test';
import assert from 'node:assert/strict';
import { parseSnapshot } from '../src/snapshot.mjs';
import { Browser } from '../src/browser.mjs';

const snapshot = `### Ran Playwright code
\`\`\`js
throw 'This is not page evidence';
\`\`\`
### Page
- Page URL: https://example.test/signup
### Snapshot
\`\`\`yaml
- main [ref=e1]:
  - heading "Create an account" [level=1] [ref=e2]
  - textbox "Full name" [ref=e3]: Ada Lovelace
  - textbox "Email: \\"work\\"" [ref=e4]: "ada@example.test"
  - spinbutton "Count" [ref=e5]: "0"
  - button "Continue" [ref=e6]:
    - text: Continue
    - generic [aria-hidden] [ref=e7]: hidden decoration
  - button "Unavailable" [disabled] [ref=e8]
  - checkbox "Updates" [checked] [ref=e9]
  - checkbox "Other" [ref=e10]
  - textbox "Empty" [ref=e11]:
    - /placeholder: Not a value
\`\`\``;

test('parses MCP evidence, quoted names, field values and state attributes', () => {
  const observation = parseSnapshot(snapshot);
  assert.equal(observation.url, 'https://example.test/signup');
  assert.equal(observation.nodes.find((node) => node.ref === 'e3').value, 'Ada Lovelace');
  assert.equal(observation.nodes.find((node) => node.ref === 'e4').name, 'Email: "work"');
  assert.equal(observation.nodes.find((node) => node.ref === 'e5').value, '0');
  assert.equal(observation.nodes.find((node) => node.ref === 'e8').disabled, true);
  assert.equal(observation.nodes.find((node) => node.ref === 'e9').checked, true);
  assert.equal(observation.nodes.find((node) => node.ref === 'e10').checked, false);
  assert.equal(observation.nodes.find((node) => node.ref === 'e11').value, '');
  assert.equal(observation.nodes.find((node) => node.ref === 'e6').text, 'Continue');
  assert.match(observation.text, /Ada Lovelace/);
  assert.doesNotMatch(observation.text, /ref=|placeholder|Not a value|hidden decoration|page evidence/);
  assert.doesNotMatch(observation.snapshot, /Ran Playwright code/);
});

test('supports bare snapshot YAML and does not treat errors as page evidence', () => {
  assert.equal(parseSnapshot('- button "Go" [ref=s2e8]').nodes[0].name, 'Go');
  assert.equal(parseSnapshot('### Error\nFailure').text, '');
});

function stubBrowser(snapshots = [snapshot]) {
  const browser = new Browser();
  const calls = [];
  let reads = 0;
  browser.call = async (name, args) => {
    calls.push({ name, args });
    return { text: name === 'browser_snapshot' ? snapshots[Math.min(reads++, snapshots.length - 1)] : '' };
  };
  return { browser, calls };
}

test('each action resolves a fresh accessibility ref without app selectors', async () => {
  const { browser, calls } = stubBrowser([snapshot, snapshot.replaceAll('e3', 's2e900')]);
  await browser.perform({ type: 'fill', target: { role: 'textbox', name: ' FULL NAME ' }, value: 'Alex' });
  await browser.perform({ type: 'fill', target: { role: 'textbox', name: 'Full name' }, value: 'Pat' });
  assert.deepEqual(calls.filter((call) => call.name === 'browser_type').map((call) => call.args.target), ['e3', 's2e900']);
  assert.equal(calls.filter((call) => call.name === 'browser_snapshot').length, 2);
});

test('missing, ambiguous, disabled or disallowed targets never silently select a node', async () => {
  const { browser, calls } = stubBrowser([snapshot.replace('- button "Unavailable"', '- button "Continue"') + '\n']);
  await assert.rejects(browser.perform({ type: 'click', target: { role: 'button', name: 'Missing' } }), { code: 'INCONCLUSIVE' });
  await assert.rejects(browser.perform({ type: 'fill', target: { role: 'button', name: 'Continue' }, value: 'hello' }), { code: 'INCONCLUSIVE' });
  const duplicate = stubBrowser(['- button "Go" [ref=e1]\n- button "Go" [ref=e2]']);
  await assert.rejects(duplicate.browser.perform({ type: 'click', target: { role: 'button', name: 'Go' } }), { code: 'INCONCLUSIVE' });
  await assert.rejects(browser.perform({ type: 'click', target: { role: 'button', name: 'Continue', selector: '#whatever' } }), { code: 'INCONCLUSIVE' });
  assert(calls.every((call) => call.name === 'browser_snapshot'));
});

test('semantic resolution is bounded to candidates and confidence threshold', async () => {
  const { browser, calls } = stubBrowser();
  let request;
  const decisions = { threshold: 0.9, async chooseTarget(value) { request = value; return { targetId: 'e6', confidence: 0.95 }; } };
  await browser.perform({ type: 'click', target: { role: 'button', intent: 'Proceed to the next step' } }, { decisions });
  assert.equal(calls.at(-1).args.target, 'e6');
  assert.deepEqual(request.candidates.map((candidate) => candidate.id), ['e6']);
  decisions.chooseTarget = async () => ({ targetId: 'invented', confidence: 1 });
  await assert.rejects(browser.perform({ type: 'click', target: { intent: 'Proceed' } }, { decisions }), { code: 'INCONCLUSIVE' });
  decisions.chooseTarget = async () => ({ targetId: 'e6', confidence: 0.89 });
  await assert.rejects(browser.perform({ type: 'click', target: { intent: 'Proceed' } }, { decisions }), { code: 'INCONCLUSIVE' });
});

test('checkbox actions preserve an already correct state', async () => {
  const { browser, calls } = stubBrowser();
  assert.equal((await browser.perform({ type: 'check', target: { name: 'Updates' }, value: true })).skipped, true);
  await browser.perform({ type: 'check', target: { name: 'Other' }, value: true });
  assert.equal(calls.filter((call) => call.name === 'browser_click').length, 1);
  assert.equal(calls.at(-1).args.target, 'e10');
});

test('MCP errors are reported and actions are never blindly retried', async () => {
  const browser = new Browser();
  let calls = 0;
  browser.client = { async callTool() { calls++; return { isError: true, content: [{ type: 'text', text: 'Navigation failed' }] }; } };
  await assert.rejects(browser.navigate('https://example.test'), { code: 'BROWSER_ERROR' });
  assert.equal(calls, 1);
  assert.equal(browser.transcript[0].isError, true);
});
