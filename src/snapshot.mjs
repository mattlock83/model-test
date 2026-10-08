// Parse the accessibility snapshot emitted by Playwright MCP. This is deliberately
// a parser for its line-oriented format, not an interpreter for arbitrary YAML.
const inputRoles = new Set(['textbox', 'searchbox', 'spinbutton', 'combobox']);
const checkRoles = new Set(['checkbox', 'switch', 'radio', 'menuitemcheckbox', 'menuitemradio']);

function scalar(value) {
  const text = value.trim();
  if (text.startsWith('"') && text.endsWith('"')) {
    try { return JSON.parse(text); } catch { return text; }
  }
  if (text.startsWith("'") && text.endsWith("'")) return text.slice(1, -1).replaceAll("''", "'");
  return text;
}

function joinText(parts) {
  return parts.filter((part, index) => part && part !== parts[index - 1]).join('\n');
}

export function parseSnapshot(raw) {
  const source = String(raw ?? '');
  const fenced = source.match(/### Snapshot\s*\n```(?:yaml|yml)?\s*\n([\s\S]*?)\n```/)
    ?? source.match(/```(?:yaml|yml)\s*\n([\s\S]*?)\n```/);
  // A tool response without a snapshot must never become page evidence.
  const snapshot = fenced ? fenced[1] : source.startsWith('###') ? '' : source;
  const url = source.match(/^- Page URL:\s*(.+)$/m)?.[1]?.trim();
  const records = [];
  const stack = [];
  const pageText = [];

  for (const line of snapshot.split('\n')) {
    const item = line.match(/^(\s*)-\s+(.+)$/);
    if (!item) continue;
    const indent = item[1].length;
    while (stack.length && stack.at(-1).indent >= indent) stack.pop();
    const parent = stack.at(-1);
    const content = item[2];
    if (content.startsWith('/')) continue; // URLs, placeholders and internal properties are not visible text.
    if (content.startsWith('text:')) {
      const text = scalar(content.slice(5));
      if (!parent?.hidden) {
        pageText.push(text);
        for (const ancestor of stack) ancestor.parts.push(text);
      }
      continue;
    }
    const roleMatch = content.match(/^([a-z][a-z0-9_-]*)\b/i);
    if (!roleMatch) continue;
    const role = roleMatch[1].toLowerCase();
    let rest = content.slice(roleMatch[0].length).trimStart();
    let name = '';
    const nameMatch = rest.match(/^"(?:\\.|[^"\\])*"/);
    if (nameMatch) {
      name = scalar(nameMatch[0]);
      rest = rest.slice(nameMatch[0].length).trimStart();
    }
    const attributes = [];
    while (rest.startsWith('[')) {
      const attribute = rest.match(/^\[([^\]]+)\]\s*/);
      if (!attribute) break;
      attributes.push(attribute[1]);
      rest = rest.slice(attribute[0].length);
    }
    if (rest && !rest.startsWith(':')) continue;
    const inline = rest.startsWith(':') ? scalar(rest.slice(1)) : '';
    const hidden = Boolean(parent?.hidden || attributes.includes('aria-hidden'));
    const ref = attributes.find((attribute) => attribute.startsWith('ref='))?.slice(4);
    const record = { indent, hidden, role, name, ref, parts: [], attributes, inline };
    stack.push(record);
    records.push(record);
    if (!hidden) {
      const parts = [name, inline].filter(Boolean);
      pageText.push(...parts);
      for (const ancestor of stack) ancestor.parts.push(...parts);
    }
  }

  const nodes = records.filter((record) => record.ref && !record.hidden).map((record) => {
    const node = { ref: record.ref, role: record.role, name: record.name, text: joinText(record.parts) };
    if (inputRoles.has(record.role)) {
      node.value = record.inline || joinText(record.parts.filter((part) => part !== record.name));
    }
    if (record.attributes.includes('disabled')) node.disabled = true;
    if (checkRoles.has(record.role)) {
      node.checked = record.attributes.includes('checked=mixed') ? 'mixed' : record.attributes.includes('checked');
    }
    if (record.attributes.includes('selected')) node.selected = true;
    return node;
  });
  return { snapshot, nodes, text: joinText(pageText), ...(url ? { url } : {}) };
}
