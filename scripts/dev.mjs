import { existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const python = path.join(
  root,
  '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
);
if (!existsSync(python)) {
  console.error('Python environment missing. Run setup.ps1 or follow README.md first.');
  process.exit(1);
}
const child = spawn(python, [path.join(root, 'scripts/launch.py'), ...process.argv.slice(2)], {
  cwd: root,
  stdio: 'inherit',
});
child.on('error', (error) => {
  console.error(error.message);
  process.exitCode = 1;
});
child.on('exit', (code) => {
  process.exitCode = code ?? 1;
});
process.on('SIGINT', () => {
  if (process.platform !== 'win32') child.kill('SIGINT');
});
process.on('SIGTERM', () => child.kill('SIGTERM'));
