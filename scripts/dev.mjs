import { createServer } from 'vite';
import { startPython } from './python.mjs';
import { setTimeout as delay } from 'node:timers/promises';

let api;
let vite;
let stopping = false;
async function healthy(url) {
  try { return (await fetch(url, { signal: AbortSignal.timeout(500) })).ok; }
  catch { return false; }
}
async function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  api?.kill();
  await vite?.close();
  process.exit(code);
}
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => void stop());
process.on('exit', () => api?.kill());
try {
  const health = 'http://127.0.0.1:8765/api/health';
  if (!(await healthy(health))) {
    api = startPython(['services/api/server.py']);
    api.on('error', (error) => { console.error(error.message); void stop(1); });
    api.on('exit', (code) => { if (!stopping) void stop(code || 1); });
    let ready = false;
    for (let i = 0; i < 100; i++) {
      if (await healthy(health)) { ready = true; break; }
      await delay(100);
    }
    if (!ready) throw new Error('本地 API 服务启动超时');
  }
  if (await healthy('http://127.0.0.1:3001/')) {
    while (await healthy('http://127.0.0.1:3001/')) await delay(2000);
    await stop();
  } else {
    vite = await createServer({ server: { host: '127.0.0.1', port: 3001, strictPort: true } });
    await vite.listen();
    vite.printUrls();
  }
} catch (error) {
  console.error(error.message);
  await stop(1);
}
