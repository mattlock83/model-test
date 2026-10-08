import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { mkdir, writeFile } from 'node:fs/promises';

const require = createRequire(import.meta.url);
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
    this.client = new Client({ name: 'model-test', version: '1.0.0' });
    await this.client.connect(this.transport);
    this.tools = (await this.client.listTools()).tools;
    return this;
  }

  async call(name, args = {}) {
    const result = await this.client.callTool({ name, arguments: args }, undefined, { timeout: 30000 });
    const text = result.content.filter((item) => item.type === 'text').map((item) => item.text).join('\n');
    this.transcript.push({ name, arguments: args, text, isError: Boolean(result.isError) });
    if (result.isError) throw new Error(`Playwright MCP ${name}: ${text}`);
    return { text, result };
  }

  async navigate(url) { return this.call('browser_navigate', { url }); }
  async click(name) {
    return this.call('browser_click', { target: `role=button[name="${name}"]`, element: name });
  }
  async fill({ name, email, seats }) {
    // All interaction goes through MCP. Selectors are bindings to this demo's form.
    for (const [target, text] of [['#full-name', name], ['#email-address', email], ['#seats', seats]]) {
      await this.call('browser_type', { target, text: String(text) });
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
