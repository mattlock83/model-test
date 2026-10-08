import test from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

test('the CLI works through a package-bin symlink', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'model-test-cli-'));
  try {
    const bin = join(directory, 'model-test');
    await symlink(fileURLToPath(new URL('../src/run.mjs', import.meta.url)), bin);
    const { stdout } = await promisify(execFile)(process.execPath, [bin, '--help']);
    assert.match(stdout, /Model-only browser testing/);
    assert.match(stdout, /--model path\/to\/model.json/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
