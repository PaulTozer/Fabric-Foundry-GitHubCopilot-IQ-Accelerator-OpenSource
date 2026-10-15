import { spawn } from 'node:child_process';
import { resolve } from 'node:path';

const python = resolve('..', '.venv', process.platform === 'win32' ? 'Scripts' : 'bin',
  process.platform === 'win32' ? 'python.exe' : 'python');
const child = spawn(python, ['-m', 'enterprise_mcp', '--http', '--host', '127.0.0.1', '--port', '8876'], {
  stdio: 'inherit',
});
child.on('error', error => { console.error(`Browser MCP startup failed: ${error.message}`); process.exitCode = 1; });
child.on('exit', code => { process.exitCode = code ?? 1; });
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
