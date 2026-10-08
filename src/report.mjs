import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);

export async function writeReport(directory, report) {
  await writeFile(resolve(directory, 'report.json'), JSON.stringify(report, null, 2));
  const html = `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Model test · ${escape(report.status)}</title><style>
body{font:16px/1.6 system-ui;background:#f4f1ea;color:#202825;max-width:1080px;margin:50px auto;padding:0 24px}h1{font-size:44px;letter-spacing:-2px}h2{margin-top:36px}.tag{background:#e2e8ff;padding:5px 12px;border-radius:20px}table{border-collapse:collapse;width:100%;background:white}th,td{text-align:left;padding:12px;border-bottom:1px solid #eee}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#fff;padding:20px}details{padding:12px;border-bottom:1px solid #ddd}small{color:#666}a{color:#304cc7}.pass{color:#256546}.fail{color:#a62f27}
</style><p>MODEL TEST / FIELDNOTES STUDIO</p><h1>${escape(report.status)} <span class="tag">${escape(report.mode)}</span></h1>
<p>GraphWalker → Playwright MCP → ${report.mode === 'openai' ? 'OpenAI Decisions' : 'deterministic offline checks'}<br>Hegel exercises form properties during the graph walk.</p>
<p>Seed <strong>${escape(report.seed)}</strong> · Edges ${report.coverage.visited}/${report.coverage.total} · Form attempts ${report.formCases.length} · ${escape(report.startedAt)}</p>
${report.mode === 'offline' ? '<p><strong>Offline verification only. No OpenAI API calls were made.</strong></p>' : ''}
${report.error ? `<h2>Failure</h2><pre>${escape(report.error)}</pre>` : ''}
<h2>Model traversal</h2><table><tr><th>#</th><th>Element</th><th>Result</th><th>Observed state</th></tr>${report.steps.map((step, i) => `<tr><td>${i + 1}</td><td>${escape(step.name)}</td><td class="${step.passed ? 'pass' : 'fail'}">${step.passed ? 'PASS' : 'FAIL'}</td><td>${escape(step.decision?.observedState || '')}</td></tr>`).join('')}</table>
<h2>Form inputs</h2>${report.formCases.map((entry, i) => `<details><summary>Case ${i + 1} · ${entry.passed ? 'PASS' : 'FAIL'} · ${escape(JSON.stringify(entry.input))}</summary><pre>${escape(JSON.stringify(entry, null, 2))}</pre></details>`).join('') || '<p>No form cases reached.</p>'}
<h2>Evidence</h2><p><a href="report.json">Full report JSON</a> · <a href="browser/mcp-transcript.json">MCP transcript</a></p><p>Checkpoint screenshots are saved in <code>browser/</code>.</p>
<small>Probabilities are model estimates, not proof. Coverage counts successfully executed graph edges; failure stops the walk.</small></html>`;
  await writeFile(resolve(directory, 'report.html'), html);
}
