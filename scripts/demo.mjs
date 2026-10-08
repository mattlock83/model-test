import { startServer } from '../src/server.mjs';
import { runFramework } from '../src/engine.mjs';
import { readOptions, helpText } from '../src/run.mjs';
import { loadModel } from '../src/model.mjs';

let server;
try {
  const options = readOptions();
  if (options.help) console.log(helpText);
  else {
    const { config } = await loadModel(options.modelPath);
    if (options.baseUrl) throw new Error('Use npm run test:model for external URLs. The demo launcher owns its local server.');
    server = await startServer();
    if (options.bug && !config.demo?.bugQuery) throw new Error('This model does not declare a demo defect query.');
    const report = await runFramework({ ...options, baseUrl: server.url, startQuery: options.bug ? config.demo.bugQuery : undefined });
    process.exitCode = report.status === 'PASS' ? 0 : report.status === 'FAIL' ? 1 : 2;
  }
} catch (error) { console.error(error.message); process.exitCode = 2; }
finally { if (server) await server.close(); }
