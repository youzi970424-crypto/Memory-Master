import { startPython } from './python.mjs';

const args = process.argv.slice(2);
if (args[0] === '--require-model') {
  args.shift();
  process.env.MEMORY_MASTER_REQUIRE_MODEL = '1';
}
try {
  const child = startPython(args);
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill());
  child.on('error', (error) => { console.error(error.message); process.exitCode = 1; });
  child.on('exit', (code) => { process.exitCode = code ?? 1; });
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
