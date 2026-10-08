import { writeFile } from 'node:fs/promises';
import { relative, resolve, sep } from 'node:path';

const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const json = (value) => escape(JSON.stringify(value, null, 2));
const list = (value) => Array.isArray(value) ? value : [];
const statusClass = (status) => ({ PASS: 'pass', FAIL: 'fail', INCONCLUSIVE: 'inconclusive' })[status] || 'neutral';
const badge = (status) => `<span class="badge ${statusClass(status)}">${escape(status || 'INCONCLUSIVE')}</span>`;
const count = (value) => escape(value ?? 0);
const probability = (value) => typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '—';

function checksHtml(checks) {
  if (!list(checks).length) return '';
  return `<ul class="checks">${checks.map((check) => `<li>${badge(check.passed === true ? 'PASS' : check.passed === false ? 'FAIL' : 'INCONCLUSIVE')} ${escape(check.description)}</li>`).join('')}</ul>`;
}

// Link only images inside the run directory. Browser evidence must never create
// an executable URL, read another local directory, or break out of an attribute.
function screenshotHtml(directory, filename, caption) {
  if (typeof filename !== 'string' || !filename) return '';
  const path = relative(resolve(directory), resolve(directory, filename));
  if (!path || path === '..' || path.startsWith(`..${sep}`) || !/\.(?:png|jpe?g|webp|gif)$/i.test(path)) return '';
  const href = path.split(sep).map(encodeURIComponent).join('/');
  return `<figure><a href="${escape(href)}"><img src="${escape(href)}" alt="${escape(caption)}" loading="lazy"></a><figcaption><a href="${escape(href)}">${escape(caption)} · open full image</a></figcaption></figure>`;
}

function coverageHtml(coverage = {}) {
  const { edges = {}, vertices = {}, requirements = {}, properties = {} } = coverage;
  const rows = [
    ['Edges', `${count(edges.visited)} / ${count(edges.total)}`, `${count(edges.percent)}%`, `${count(edges.required)}%`, list(edges.ids)],
    ['Vertices', `${count(vertices.visited)} / ${count(vertices.total)}`, `${count(vertices.percent)}%`, `${count(vertices.required)}%`, list(vertices.ids)],
    ['Requirements', `${count(requirements.visited)} / ${count(requirements.total)}`, requirements.total ? `${(requirements.visited / requirements.total * 100).toFixed(1)}%` : '—', 'Model-defined IDs', list(requirements.ids)],
    ['Property suites', `${list(properties.completed).length} / ${count(properties.total)}`, '—', 'All declared suites', list(properties.completed)],
  ];
  return `<div class="table-wrap"><table><thead><tr><th>Coverage</th><th>Verified</th><th>Reached</th><th>Required</th></tr></thead><tbody>${rows.map(([label, visited, percent, required, ids]) => `<tr><th>${label}</th><td>${visited}${ids.length ? `<details><summary>IDs</summary><pre>${json(ids)}</pre></details>` : ''}</td><td>${percent}</td><td>${required}</td></tr>`).join('')}</tbody></table></div>
  ${list(requirements.missing).length ? `<p class="muted">Unvisited requirement IDs: ${escape(requirements.missing.join(', '))}</p>` : ''}
  <p class="muted">An edge earns coverage after its destination checkpoint passes. Action rows describe execution; they do not independently establish destination coverage.</p>`;
}

function stepHtml(directory, step, index) {
  const metrics = [
    ['Observed state', step.observedState], ['Check source', step.source],
    ['Assertion probability', typeof step.passProbability === 'number' ? probability(step.passProbability) : undefined],
    ['State confidence', typeof step.stateConfidence === 'number' ? probability(step.stateConfidence) : undefined],
    ['Selected state probability', typeof step.stateProbability === 'number' ? probability(step.stateProbability) : undefined],
  ].filter(([, value]) => value !== undefined && value !== null);
  return `<details class="entry"${step.status === 'FAIL' || step.status === 'INCONCLUSIVE' ? ' open' : ''}>
  <summary><span class="index">${index + 1}</span>${badge(step.status)} <strong>${escape(step.name || step.id)}</strong> <span class="muted">${step.type === 'edge' ? 'action' : 'state checkpoint'}</span></summary>
  ${metrics.length ? `<dl class="metrics">${metrics.map(([label, value]) => `<div><dt>${label}</dt><dd>${escape(value)}</dd></div>`).join('')}</dl>` : ''}
  ${step.error ? `<pre class="error">${escape(step.error)}</pre>` : ''}${checksHtml(step.checks)}
  ${screenshotHtml(directory, step.screenshot, `Checkpoint ${step.name || step.id}`)}
  ${step.evidence ? `<details><summary>Observed browser evidence</summary><pre>${json(step.evidence)}</pre></details>` : ''}
  </details>`;
}

function caseHtml(entry, index) {
  return `<details class="entry"${entry.status === 'FAIL' || entry.status === 'INCONCLUSIVE' ? ' open' : ''}>
  <summary><span class="index">${index + 1}</span>${badge(entry.status)} <strong>${escape(entry.suite || 'Property case')}</strong> <span class="muted">${escape(entry.source)}</span></summary>
  <pre>${json(entry.input)}</pre><p>Expected state: <code>${escape(entry.expectedState || '—')}</code> · Observed state: <code>${escape(entry.observedState || '—')}</code></p>
  ${entry.error ? `<pre class="error">${escape(entry.error)}</pre>` : ''}${checksHtml(entry.checks)}
  <details><summary>Full case and browser evidence</summary><pre>${json(entry)}</pre></details></details>`;
}

export async function writeReport(directory, report) {
  await writeFile(resolve(directory, 'report.json'), JSON.stringify(report, null, 2));
  const cases = list(report.formCases), steps = list(report.steps), resolutions = list(report.resolutions);
  const stats = report.decisions || {};
  const outcomes = ['PASS', 'FAIL', 'INCONCLUSIVE'].map((status) => `${cases.filter((entry) => entry.status === status).length} ${status.toLowerCase()}`).join(' · ');
  const semanticNote = report.provider === 'offline'
    ? 'Offline verification uses deterministic model assertions. No LLM judgment is used.'
    : stats.calls > 0
      ? 'The configured Decisions-compatible provider was called. Each checkpoint shows its actual check source; Hegel reductions use deterministic model requirements.'
      : 'No decision API requests were made in this run. Semantic checks were not reached or an exact cached result was sufficient.';
  const counterexample = report.counterexample;
  const counterexampleTitle = counterexample?.status === 'FAIL'
    ? (counterexample.source?.startsWith('hegel:') ? 'Hegel-reduced counterexample' : 'Failing input')
    : 'Interrupted input';
  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escape(report.model || 'Model test')} · ${escape(report.status)}</title>
<style>
:root{color-scheme:light;--ink:#172b30;--muted:#617277;--line:#dce4e4;--paper:#fff;--back:#f2f6f5;--link:#175ca6}*{box-sizing:border-box}body{font:15px/1.65 system-ui,-apple-system,sans-serif;background:var(--back);color:var(--ink);max-width:1150px;margin:40px auto;padding:0 24px 60px}h1{font-size:clamp(30px,5vw,48px);letter-spacing:-1.5px;line-height:1.1;margin:16px 0}h2{font-size:24px;margin:0 0 16px}h3{font-size:18px}.eyebrow{font-size:12px;letter-spacing:2px;font-weight:700;color:var(--muted)}header{margin-bottom:28px}section{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:24px;margin:20px 0}p{margin:12px 0}.muted,dt,figcaption{color:var(--muted)}.badge{display:inline-block;font-size:11px;letter-spacing:.6px;font-weight:750;border-radius:5px;padding:3px 8px;vertical-align:middle}.pass{color:#086749;background:#ddf3e9}.fail{color:#a02027;background:#fde7e8}.inconclusive{color:#805309;background:#fff0cd}.neutral{color:#42545b;background:#e8edf0}.summary{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:14px;margin:20px 0}.stat{padding:14px 16px;background:var(--back);border-radius:8px}.stat strong{display:block;font-size:25px;line-height:1.3}.stat span{font-size:12px;color:var(--muted)}table{border-collapse:collapse;width:100%}th,td{padding:12px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}thead{background:var(--back)}.table-wrap{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--back);padding:14px;border-radius:8px;font-size:12px;line-height:1.6}code{font-size:12px;overflow-wrap:anywhere}.error{background:#fff4f2}details{margin:10px 0}summary{cursor:pointer;overflow-wrap:anywhere}summary strong{margin-left:5px}.entry{border-top:1px solid var(--line);padding:14px 0;margin:0}.entry:last-child{padding-bottom:0}.index{display:inline-block;min-width:30px;color:var(--muted)}.checks{list-style:none;padding:0}.checks li{padding:7px 0;border-bottom:1px solid #edf1f1}.metrics{display:flex;flex-wrap:wrap;gap:12px 28px}.metrics div{min-width:130px}dt{font-size:12px}dd{margin:0;font-weight:600}a{color:var(--link);text-underline-offset:3px}.artifacts{display:flex;flex-wrap:wrap;gap:12px 22px}figure{margin:18px 0}img{display:block;max-width:100%;max-height:420px;border:1px solid var(--line);border-radius:8px}figcaption{font-size:12px;margin-top:6px}footer{font-size:12px;color:var(--muted)}@media(max-width:600px){body{padding:0 12px 40px;margin-top:24px}section{padding:18px}.entry summary .muted{display:block;margin-left:30px}th,td{padding:9px}}
</style></head><body>
<header><p class="eyebrow">MODEL TEST / EXECUTION REPORT</p><h1>${escape(report.model || 'GraphWalker model')}</h1>
<div class="summary">${badge(report.status)} <span>Provider: <strong>${escape(report.provider || 'unknown')}</strong></span><span>· Seed ${escape(report.seed)}</span>${report.replay ? '<span>· Counterexample replay</span>' : ''}</div>
<p class="muted">Started ${escape(report.startedAt)}${report.finishedAt ? ` · Finished ${escape(report.finishedAt)}` : ''}</p>
${report.startUrl ? `<p>Target: <code>${escape(report.startUrl)}</code></p>` : ''}</header>
${report.error || report.cleanupError || report.captureError ? `<section aria-label="Run outcome"><h2>${escape(report.status === 'FAIL' ? 'Failure' : 'Run could not complete conclusively')}</h2>${report.reason ? `<p>Reason: <code>${escape(report.reason)}</code></p>` : ''}${report.error ? `<pre class="error">${escape(report.error)}</pre>` : ''}${report.cleanupError ? `<p>Cleanup error</p><pre class="error">${escape(report.cleanupError)}</pre>` : ''}${report.captureError ? `<p>Evidence capture error</p><pre>${escape(report.captureError)}</pre>` : ''}${screenshotHtml(directory, report.failureScreenshot, 'Failure evidence')}</section>` : ''}
${counterexample ? `<section><h2>${counterexampleTitle}</h2><p>${badge(counterexample.status)} Suite <strong>${escape(counterexample.suite)}</strong> · ${escape(counterexample.source)}</p><pre>${json(counterexample.input)}</pre>${checksHtml(counterexample.checks)}<p><a href="replay.json">Replay input</a> · <a href="model.json">Exact saved model</a></p><p class="muted">Replay this input with the saved model. A property counterexample demonstrates a violated requirement; an interrupted input records uncertainty.</p></section>` : ''}
<section><h2>Verified coverage</h2>${coverageHtml(report.coverage)}${report.replay ? '<p class="muted">This run replayed one input; full graph coverage was not attempted.</p>' : ''}</section>
<section><h2>Decision usage</h2><div class="stats">${[['API requests', stats.calls], ['Cache hits', stats.cacheHits], ['Input tokens', stats.inputTokens], ['Output tokens', stats.outputTokens], ['Total tokens', stats.totalTokens]].map(([label, value]) => `<div class="stat"><strong>${count(value)}</strong><span>${label}</span></div>`).join('')}</div><p>${semanticNote}</p><p class="muted">Probabilities are provider estimates. Uncertain answers, exhausted budgets, and infrastructure errors produce INCONCLUSIVE results.</p></section>
<section><h2>Graph traversal</h2>${steps.length ? steps.map((step, index) => stepHtml(directory, step, index)).join('') : '<p>No graph elements were executed.</p>'}</section>
<section><h2>Hegel and input cases</h2><p>${cases.length} recorded attempts · ${outcomes}</p><p class="muted">Attempts include generated inputs, deterministic boundaries, model examples, and Hegel reduction or replay attempts when present.</p>${cases.length ? cases.map(caseHtml).join('') : '<p>No property cases were reached.</p>'}</section>
${resolutions.length ? `<section><h2>Browser target resolution</h2><p>${resolutions.filter((entry) => entry.method === 'exact').length} exact accessible-name matches · ${resolutions.filter((entry) => entry.method === 'decision').length} decision-assisted resolutions</p><details><summary>Resolution evidence</summary><pre>${json(resolutions)}</pre></details></section>` : ''}
<section><h2>Saved evidence</h2><nav class="artifacts" aria-label="Report artifacts"><a href="report.json">Full report JSON</a><a href="model.json">Saved model JSON</a><a href="browser/mcp-transcript.json">Playwright MCP transcript</a>${counterexample ? '<a href="replay.json">Replay input JSON</a>' : ''}</nav>${report.modelHash ? `<p class="muted">Model SHA-256: <code>${escape(report.modelHash)}</code></p>` : ''}${report.failureEvidence ? `<details><summary>Final browser evidence</summary><pre>${json(report.failureEvidence)}</pre></details>` : ''}</section>
<footer>GraphWalker selects the model path. Playwright MCP executes browser actions. Hegel exercises model-declared input properties. Coverage describes the modeled requirements exercised in this run and does not establish that every possible behavior is correct.</footer>
</body></html>`;
  await writeFile(resolve(directory, 'report.html'), html);
}
