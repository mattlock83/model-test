import { spawn } from 'node:child_process';
import { mkdir, readFile, writeFile, chmod, access } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

// Official build instructions:
// https://graphwalker.github.io/graphwalker-rs/getting-started.html
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const tools = resolve(root, '.tools');
const source = resolve(tools, 'graphwalker-rs');
const lockPath = resolve(root, 'graphwalker.lock.json');
const repository = 'https://github.com/GraphWalker/graphwalker-rs.git';
const exists = (path) => access(path).then(() => true, () => false);

function run(command, args, { cwd = root, env = process.env, capture = false } = {}) {
  return new Promise((done, reject) => {
    const child = spawn(command, args, { cwd, env, stdio: capture ? ['ignore', 'pipe', 'pipe'] : 'inherit' });
    let output = '';
    if (capture) {
      child.stdout.on('data', (data) => { output += data; });
      child.stderr.on('data', (data) => { output += data; });
    }
    child.once('error', reject);
    child.once('exit', (code) => code === 0 ? done(output.trim()) : reject(new Error(`${command} exited with ${code}${output ? `: ${output}` : ''}`)));
  });
}

async function setup() {
  await mkdir(tools, { recursive: true });
  let lock;
  try { lock = JSON.parse(await readFile(lockPath, 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (lock && !/^[0-9a-f]{40}$/.test(lock.revision)) throw new Error('Invalid GraphWalker revision in graphwalker.lock.json.');
  if (!(await exists(resolve(source, '.git')))) {
    await mkdir(source, { recursive: true });
    await run('git', ['init', source]);
    await run('git', ['remote', 'add', 'origin', repository], { cwd: source });
  }
  const target = lock?.revision || 'main';
  if (await run('git', ['status', '--porcelain'], { cwd: source, capture: true })) {
    throw new Error('The GraphWalker source checkout has local edits. Preserve them before rerunning setup.');
  }
  let current = await run('git', ['rev-parse', 'HEAD'], { cwd: source, capture: true }).catch(() => '');
  if (current !== target) {
    await run('git', ['fetch', '--depth', '1', 'origin', target], { cwd: source });
    await run('git', ['checkout', '--detach', 'FETCH_HEAD'], { cwd: source });
    current = await run('git', ['rev-parse', 'HEAD'], { cwd: source, capture: true });
  }
  if (!lock) {
    await writeFile(lockPath, JSON.stringify({ repository, revision: current }, null, 2) + '\n');
  }
  console.log(`GraphWalker source: ${current}`);

  const localCargo = resolve(tools, 'cargo', 'bin', process.platform === 'win32' ? 'cargo.exe' : 'cargo');
  let cargo = 'cargo';
  let env = { ...process.env };
  const localEnvironment = () => ({ ...process.env, CARGO_HOME: resolve(tools, 'cargo'), RUSTUP_HOME: resolve(tools, 'rustup'), PATH: `${dirname(localCargo)}${process.platform === 'win32' ? ';' : ':'}${process.env.PATH}` });
  if (await exists(localCargo)) { cargo = localCargo; env = localEnvironment(); }
  let version = await run(cargo, ['--version'], { env, capture: true }).catch(() => '');
  if (!version) {
    const targets = { 'darwin-arm64': 'aarch64-apple-darwin', 'darwin-x64': 'x86_64-apple-darwin', 'linux-x64': 'x86_64-unknown-linux-gnu', 'linux-arm64': 'aarch64-unknown-linux-gnu' };
    const targetTriple = targets[`${process.platform}-${process.arch}`];
    if (!targetTriple) throw new Error('Install Rust 1.88+ from https://rustup.rs, then rerun npm run setup.');
    const installer = resolve(tools, 'rustup-init');
    console.log('Installing a minimal Rust toolchain inside .tools/ (no shell profile changes).');
    const response = await fetch(`https://static.rust-lang.org/rustup/dist/${targetTriple}/rustup-init`, { signal: AbortSignal.timeout(120000) });
    if (!response.ok) throw new Error(`Rust installer download returned HTTP ${response.status}.`);
    await writeFile(installer, new Uint8Array(await response.arrayBuffer()));
    await chmod(installer, 0o755);
    env = localEnvironment();
    await run(installer, ['-y', '--no-modify-path', '--profile', 'minimal', '--default-toolchain', 'stable'], { env });
    cargo = localCargo;
    version = await run(cargo, ['--version'], { env, capture: true });
  }
  console.log(version);
  console.log('Building the GraphWalker CLI with locked dependencies…');
  await run(cargo, ['build', '--release', '--locked', '--bin', 'graphwalker', '--target-dir', resolve(source, 'target')], { cwd: source, env });
  const binary = resolve(source, 'target', 'release', process.platform === 'win32' ? 'graphwalker.exe' : 'graphwalker');
  await run(binary, ['check', '-g', resolve(root, 'models/booking.json')]);
  console.log(`Ready: ${binary}`);
}

setup().catch((error) => { console.error(error.message); process.exitCode = 1; });
