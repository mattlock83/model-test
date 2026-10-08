import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { mkdir, writeFile } from 'node:fs/promises';
import { parseSnapshot } from './snapshot.mjs';

const require = createRequire(import.meta.url);
const rolesByAction = {
  click: new Set(['button', 'link', 'checkbox', 'radio', 'switch', 'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'treeitem', 'option']),
  fill: new Set(['textbox', 'searchbox', 'spinbutton', 'combobox']),
  select: new Set(['combobox', 'listbox']),
  check: new Set(['checkbox', 'switch', 'radio', 'menuitemcheckbox', 'menuitemradio']),
};
const normalize = (value) => String(value ?? '').trim().replace(/\s+/g, ' ').toLowerCase();
function failure(message, code = 'INCONCLUSIVE') {
  return Object.assign(new Error(message), { code });
}

export class Browser {
  constructor({ outputDir, headed = false } = {}) {
    this.outputDir = resolve(outputDir || 'artifacts/browser');
    this.headed = headed;
    this.transcript = [];
  }

  async start() {
    await mkdir(this.outputDir, { recursive: true });
    const cli = resolve(dirname(require.resolve('@playwright/mcp/package.json')), 'cli.js');
    const args = [cli, '--isolated', '--browser', 'chrome', '--timeout-settle', '0', '--output-dir', this.outputDir];
    if (!this.headed) args.push('--headless');
    if (process.env.PLAYWRIGHT_EXECUTABLE_PATH) args.push('--executable-path', process.env.PLAYWRIGHT_EXECUTABLE_PATH);
    this.transport = new StdioClientTransport({ command: process.execPath, args, stderr: 'pipe' });
    this.transport.stderr?.on('data', (data) => { this.stderr = (this.stderr || '') + data.toString(); });
    this.client = new Client({ name: 'model-test', version: '2.0.0' });
    await this.client.connect(this.transport);
    this.tools = (await this.client.listTools()).tools;
    return this;
  }

  async call(name, args = {}) {
    let result;
    try {
      result = await this.client.callTool({ name, arguments: args }, undefined, { timeout: 30000 });
    } catch (error) {
      this.transcript.push({ name, arguments: args, text: error.message, isError: true });
      throw failure(`Playwright MCP ${name}: ${error.message}`, 'BROWSER_ERROR');
    }
    const text = result.content.filter((item) => item.type === 'text').map((item) => item.text).join('\n');
    this.transcript.push({ name, arguments: args, text, isError: Boolean(result.isError) });
    if (result.isError) throw failure(`Playwright MCP ${name}: ${text}`, 'BROWSER_ERROR');
    return { text, result };
  }

  async navigate(url) { return this.call('browser_navigate', { url }); }

  async observe() {
    this.latestObservation = parseSnapshot(await this.snapshot());
    return this.latestObservation;
  }

  async resolveTarget(target, decisions, { type, context, onResolution } = {}) {
    if (!target || typeof target !== 'object' || (!target.name && !target.intent)) {
      throw failure('An action target needs an accessible name or an intent.');
    }
    if (Object.keys(target).some((key) => !['role', 'name', 'intent'].includes(key))) {
      throw failure('Targets may contain only role, name and intent; selectors and executable code are not supported.');
    }
    const observation = await this.observe();
    const roles = rolesByAction[type];
    const candidates = observation.nodes.filter((node) => !node.disabled
      && (!roles || roles.has(node.role))
      && (!target.role || normalize(node.role) === normalize(target.role)));
    const exact = target.name ? candidates.filter((node) => normalize(node.name) === normalize(target.name)) : [];
    if (exact.length === 1) {
      await onResolution?.({ method: 'exact', target, node: exact[0] });
      return exact[0];
    }
    if (!target.intent || typeof decisions?.chooseTarget !== 'function') {
      throw failure(`Cannot uniquely resolve ${JSON.stringify(target)}: ${exact.length} exact matches. Provide a specific accessible name or use an intent with a decision provider.`);
    }
    const choices = exact.length > 1 ? exact : candidates;
    if (!choices.length) throw failure(`No eligible ${type || 'interaction'} targets for ${JSON.stringify(target)}.`);
    const decision = await decisions.chooseTarget({
      intent: target.intent,
      candidates: choices.map((node) => ({ id: node.ref, role: node.role, name: node.name, description: node.text })),
      snapshot: observation.snapshot,
      context,
    });
    const selected = choices.find((node) => node.ref === decision?.targetId);
    if (!selected || !Number.isFinite(decision.confidence) || decision.confidence < (decisions.threshold ?? 0.85)) {
      throw failure(`Decision provider could not confidently resolve ${JSON.stringify(target)}.`);
    }
    await onResolution?.({ method: 'decision', target, node: selected, decision });
    return selected;
  }

  async perform(action, { context, decisions, onResolution } = {}) {
    if (!action || !['click', 'fill', 'select', 'check', 'press', 'navigate'].includes(action.type)) {
      throw failure(`Unsupported browser action: ${action?.type}`);
    }
    if (action.type === 'navigate') {
      let url;
      try { url = new URL(action.url, context?.baseUrl); } catch { throw failure('Navigation requires a valid URL.'); }
      if (!['http:', 'https:'].includes(url.protocol)) throw failure('Only HTTP and HTTPS navigation is supported.');
      return this.navigate(url.href);
    }
    if (action.type === 'press' && !action.target) return this.call('browser_press_key', { key: action.key });
    const node = await this.resolveTarget(action.target, decisions, { type: action.type, context, onResolution });
    const args = { target: node.ref, element: `${node.role} ${node.name}`.trim() };
    switch (action.type) {
      case 'click': return this.call('browser_click', args);
      case 'fill': return this.call('browser_type', { ...args, text: String(action.value ?? '') });
      case 'select': return this.call('browser_select_option', { ...args, values: (Array.isArray(action.value) ? action.value : [action.value]).map(String) });
      case 'check': {
        const desired = action.value ?? true;
        if (typeof desired !== 'boolean') throw failure('Check actions require a boolean value.');
        if (node.checked === desired) return { skipped: true, reason: 'Already in the requested state.' };
        if (!desired && ['radio', 'menuitemradio'].includes(node.role)) throw failure('Radio buttons cannot be unchecked directly. Select another option.');
        return this.call('browser_click', args);
      }
      case 'press':
        await this.call('browser_click', args);
        return this.call('browser_press_key', { key: action.key });
    }
  }

  async snapshot() { return (await this.call('browser_snapshot')).text; }
  async screenshot(filename) {
    await this.call('browser_take_screenshot', { filename: resolve(this.outputDir, filename), fullPage: true, scale: 'css' });
    return resolve(this.outputDir, filename);
  }
  async close() {
    try { if (this.client) await this.client.close(); }
    finally {
      if (this.transport) await this.transport.close();
      await mkdir(this.outputDir, { recursive: true });
      await writeFile(resolve(this.outputDir, 'mcp-transcript.json'), JSON.stringify(this.transcript, null, 2));
    }
  }
}
