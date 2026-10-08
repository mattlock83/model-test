#!/usr/bin/env node
import { parseArgs } from 'node:util';
import { fileURLToPath } from 'node:url';
import { realpathSync } from 'node:fs';
import { runFramework } from './engine.mjs';

export function readOptions(args = process.argv.slice(2)) {
  const { values } = parseArgs({ args, options: {
    model: { type: 'string' }, url: { type: 'string' }, provider: { type: 'string', default: 'openai' },
    offline: { type: 'boolean', default: false }, headed: { type: 'boolean', default: false },
    seed: { type: 'string', default: '42' }, cases: { type: 'string' },
    'max-steps': { type: 'string', default: '200' }, 'max-calls': { type: 'string', default: '80' },
    threshold: { type: 'string', default: '0.85' }, output: { type: 'string', default: 'artifacts' },
    replay: { type: 'string' }, bug: { type: 'boolean', default: false }, help: { type: 'boolean', default: false },
  } });
  return { help: values.help, bug: values.bug, modelPath: values.model, baseUrl: values.url,
    provider: values.offline ? 'offline' : values.provider, headed: values.headed, seed: Number(values.seed),
    cases: values.cases === undefined ? undefined : Number(values.cases), maxSteps: Number(values['max-steps']),
    maxCalls: Number(values['max-calls']), threshold: Number(values.threshold), outputDir: values.output, replayPath: values.replay,
  };
}

export const helpText = `Model-only browser testing
  npm run test:model -- --model path/to/model.json --url https://your-site.test
  npm run test:model -- --model path/to/model.json --url http://localhost:3000 --offline
Options: --provider openai|offline --seed 42 --cases 12 --headed --max-steps 200
         --max-calls 80 --threshold 0.85 --output artifacts --replay path/to/replay.json
The model owns actions, expected states, input constraints, property suites and coverage.
Offline mode requires unambiguous accessible controls and deterministic state assertions.`;

if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const options = readOptions();
    if (options.help) console.log(helpText);
    else {
      if (options.bug) throw new Error('--bug belongs to the bundled demo launcher, not the framework.');
      const report = await runFramework(options);
      process.exitCode = report.status === 'PASS' ? 0 : report.status === 'FAIL' ? 1 : 2;
    }
  } catch (error) { console.error(error.message); process.exitCode = 2; }
}
