import { execFile } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const runFile = promisify(execFile);
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const defaultBinary = resolve(root, '.tools/graphwalker-rs/target/release', process.platform === 'win32' ? 'graphwalker.exe' : 'graphwalker');

function positiveInteger(value, name) {
  if (!Number.isSafeInteger(value) || value < 1) throw new Error(`${name} must be a positive safe integer.`);
}

function coverageThreshold(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 100) {
    throw new Error('requiredEdgeCoverage must be a finite number between 0 and 100.');
  }
}

/** Validate native path identity/adjacency and the explicitly required coverage. */
export function parsePath(document, stdout, { maxSteps = 100, requiredEdgeCoverage = 100 } = {}) {
  positiveInteger(maxSteps, 'maxSteps');
  coverageThreshold(requiredEdgeCoverage);
  if (!Array.isArray(document.models) || document.models.length !== 1) {
    throw new Error('The framework supports exactly one GraphWalker model.');
  }
  const model = document.models[0];
  const byName = new Map();
  const byId = new Map();
  for (const [type, elements] of [['vertex', model.vertices], ['edge', model.edges]]) {
    if (!Array.isArray(elements) || !elements.length) throw new Error(`The model needs ${type} elements.`);
    for (const element of elements) {
      if (typeof element.id !== 'string' || !element.id || typeof element.name !== 'string' || !element.name) {
        throw new Error('Every model element needs a nonempty id and name.');
      }
      if (byId.has(element.id)) throw new Error(`Duplicate model element id: ${element.id}`);
      if (byName.has(element.name)) throw new Error(`Duplicate model element name: ${element.name}`);
      const typed = { ...element, type };
      byName.set(element.name, typed);
      byId.set(element.id, typed);
    }
  }
  const start = byId.get(model.startElementId);
  if (!start) throw new Error('The model needs a valid startElementId.');
  for (const edge of model.edges) {
    if (byId.get(edge.targetVertexId)?.type !== 'vertex') throw new Error(`Unknown target vertex on ${edge.name}.`);
    if (edge.sourceVertexId != null && byId.get(edge.sourceVertexId)?.type !== 'vertex') {
      throw new Error(`Unknown source vertex on ${edge.name}.`);
    }
  }
  const lines = stdout.split(/\r?\n/u).filter((line) => line.trim());
  if (!lines.length) throw new Error('GraphWalker emitted an empty path.');
  if (lines.length > maxSteps) throw new Error(`GraphWalker path exceeds maxSteps (${lines.length} > ${maxSteps}); coverage was not executed.`);
  const path = lines.map((line, index) => {
    let entry;
    try { entry = JSON.parse(line); }
    catch { throw new Error(`Invalid GraphWalker JSON on output line ${index + 1}.`); }
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
      throw new Error(`Invalid GraphWalker output record on line ${index + 1}.`);
    }
    const element = byName.get(entry.currentElementName);
    if (!element) throw new Error(`GraphWalker emitted unknown element: ${String(entry.currentElementName)}`);
    // Rust's offline CLI uses Id; the legacy REST interface uses ID.
    const emittedId = entry.currentElementId ?? entry.currentElementID;
    if (emittedId != null && emittedId !== element.id) {
      throw new Error(`GraphWalker name/id mismatch for ${element.name}.`);
    }
    if (model.id != null && entry.modelId != null && entry.modelId !== model.id) {
      throw new Error(`GraphWalker emitted an unexpected model id: ${entry.modelId}.`);
    }
    if (entry.data !== undefined && (entry.data === null || (typeof entry.data !== 'string' &&
      (typeof entry.data !== 'object' || Array.isArray(entry.data))))) {
      throw new Error(`Invalid GraphWalker data on output line ${index + 1}.`);
    }
    // Native verbose data is currently a display string, not a typed variable
    // object. Preserve it as evidence; do not reinterpret or execute scripts.
    return { ...element, ...(entry.data === undefined ? {} : { graphData: structuredClone(entry.data) }) };
  });
  if (path[0].id !== start.id) throw new Error(`GraphWalker path must start at ${start.name}.`);
  for (let i = 1; i < path.length; i++) {
    const previous = path[i - 1];
    const current = path[i];
    if (previous.type === current.type || (current.type === 'edge'
      ? current.sourceVertexId !== previous.id
      : previous.targetVertexId !== current.id)) {
      throw new Error(`Invalid GraphWalker transition: ${previous.name} → ${current.name}.`);
    }
  }
  const visited = new Set(path.filter((element) => element.type === 'edge').map((element) => element.id));
  const missing = model.edges.filter((edge) => !visited.has(edge.id));
  const edgeCoverage = visited.size / model.edges.length * 100;
  if (edgeCoverage < requiredEdgeCoverage) {
    throw new Error(`GraphWalker path has incomplete edge coverage (${edgeCoverage.toFixed(2)}% < required ${requiredEdgeCoverage}%); missing: ${missing.map((edge) => edge.name).join(', ')}.`);
  }
  // A final edge still needs its destination checked, even if the generator's stop
  // condition fires before emitting that vertex. This is a verification checkpoint,
  // not an additional generated transition.
  if (path.at(-1).type === 'edge') {
    if (path.length >= maxSteps) throw new Error(`GraphWalker path needs a final vertex checkpoint beyond maxSteps (${maxSteps}).`);
    path.push({ ...byId.get(path.at(-1).targetVertexId), completionCheckpoint: true });
  }
  return path;
}

/** Generate the actual GraphWalker Rust CLI path with a reproducible nonzero seed. */
export async function generatePath({ modelPath, seed = 42, maxSteps = 100, requiredEdgeCoverage = 100 }) {
  positiveInteger(seed, 'seed'); // GraphWalker uses zero to request a random seed.
  positiveInteger(maxSteps, 'maxSteps');
  coverageThreshold(requiredEdgeCoverage);
  const absoluteModel = resolve(modelPath);
  const document = JSON.parse(await readFile(absoluteModel, 'utf8'));
  const binary = process.env.GRAPHWALKER_BIN || defaultBinary;
  let stdout;
  try {
    ({ stdout } = await runFile(binary, ['offline', '-g', absoluteModel, '-s', String(seed), '-o'], {
      timeout: 60_000, maxBuffer: 4 * 1024 * 1024, encoding: 'utf8',
    }));
  } catch (error) {
    if (error.code === 'ENOENT') throw new Error(`GraphWalker binary not found at ${binary}. Run npm run setup or set GRAPHWALKER_BIN.`, { cause: error });
    if (error.killed) throw new Error('GraphWalker exceeded the 60-second generation timeout.', { cause: error });
    throw new Error(`GraphWalker failed: ${(error.stderr || error.message).trim()}`, { cause: error });
  }
  return parsePath(document, stdout, { maxSteps, requiredEdgeCoverage });
}
